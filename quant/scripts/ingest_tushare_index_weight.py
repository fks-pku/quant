"""Ingest Tushare index_weight data into a sidecar DuckDB."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import duckdb
import pandas as pd

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR.parent))

from quant.scripts.ingest_tushare_daily_basic import TushareClient


logger = logging.getLogger("ingest_tushare_index_weight")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DUCKDB_DIR = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "live"
DEFAULT_WEIGHT_DB = DEFAULT_DUCKDB_DIR / "cn_index_weight.duckdb"
INDEX_WEIGHT_TABLE = "cn_index_weight"
INDEX_WEIGHT_COLUMNS = ["index_code", "trade_date", "symbol", "ts_code", "weight", "updated_at"]


@dataclass(frozen=True)
class IndexWeightIngestSummary:
    index_code: str
    start: date
    end: date
    chunks: int
    fetched_rows: int
    fetched_dates: int
    coverage: Dict[str, Any]


def ingest_tushare_index_weight(
    index_code: str = "000300.SH",
    weight_db_path: Path = DEFAULT_WEIGHT_DB,
    start_date: str = "2016-01-01",
    end_date: Optional[str] = None,
    min_interval: float = 0.25,
    timeout: int = 20,
    chunk_days: int = 3000,
    dry_run: bool = False,
) -> IndexWeightIngestSummary:
    start = _parse_date(start_date)
    end = _parse_date(end_date or _default_end_date())
    if end < start:
        raise ValueError(f"end_date {end} must be >= start_date {start}")
    chunks = list(_date_chunks(start, end, max(1, int(chunk_days))))
    frames: List[pd.DataFrame] = []
    if not dry_run:
        client = TushareClient(min_interval=min_interval, timeout=timeout)
        for chunk_start, chunk_end in chunks:
            frame = client.call(
                "index_weight",
                index_code=index_code,
                start_date=_fmt_tushare_date(chunk_start),
                end_date=_fmt_tushare_date(chunk_end),
            )
            normalized = _normalize_index_weight_frame(frame, index_code)
            if not normalized.empty:
                frames.append(normalized)
            logger.info("Fetched %s rows for %s to %s", len(normalized), chunk_start, chunk_end)
    combined = pd.concat(frames, ignore_index=True) if frames else _empty_frame()
    combined = combined.drop_duplicates(["index_code", "trade_date", "symbol"], keep="last")
    combined = combined.sort_values(["index_code", "trade_date", "symbol"]).reset_index(drop=True)
    if not dry_run:
        weight_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(weight_db_path))
        try:
            _ensure_schema(conn)
            _replace_rows(conn, combined, index_code, start, end)
            coverage = _coverage_summary(conn, index_code, start, end)
        finally:
            conn.close()
    else:
        coverage = _frame_coverage(combined)
    return IndexWeightIngestSummary(
        index_code=index_code,
        start=start,
        end=end,
        chunks=len(chunks),
        fetched_rows=len(combined),
        fetched_dates=int(combined["trade_date"].nunique()) if not combined.empty else 0,
        coverage=coverage,
    )


def _ensure_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {INDEX_WEIGHT_TABLE} (
            index_code VARCHAR,
            trade_date DATE,
            symbol VARCHAR,
            ts_code VARCHAR,
            weight DOUBLE,
            updated_at TIMESTAMP
        )
        """
    )
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{INDEX_WEIGHT_TABLE}_key
        ON {INDEX_WEIGHT_TABLE}(index_code, trade_date, symbol)
        """
    )


def _replace_rows(
    conn: duckdb.DuckDBPyConnection,
    frame: pd.DataFrame,
    index_code: str,
    start: date,
    end: date,
) -> None:
    conn.execute(
        f"DELETE FROM {INDEX_WEIGHT_TABLE} WHERE index_code = ? AND trade_date BETWEEN ? AND ?",
        [index_code, start, end],
    )
    if frame.empty:
        return
    conn.register("index_weight_frame", frame[INDEX_WEIGHT_COLUMNS])
    try:
        conn.execute(
            f"""
            INSERT INTO {INDEX_WEIGHT_TABLE} ({", ".join(INDEX_WEIGHT_COLUMNS)})
            SELECT {", ".join(INDEX_WEIGHT_COLUMNS)}
            FROM index_weight_frame
            """
        )
    finally:
        conn.unregister("index_weight_frame")


def _normalize_index_weight_frame(frame: pd.DataFrame, index_code: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return _empty_frame()
    required = {"con_code", "trade_date", "weight"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"index_weight response missing columns: {sorted(missing)}")
    result = pd.DataFrame()
    result["index_code"] = frame.get("index_code", index_code).fillna(index_code).astype(str)
    result["trade_date"] = pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d", errors="coerce").dt.date
    result["ts_code"] = frame["con_code"].astype(str)
    result["symbol"] = result["ts_code"].map(_from_ts_code)
    result["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
    result["updated_at"] = datetime.utcnow()
    result = result.dropna(subset=["trade_date", "symbol", "weight"])
    result = result[result["weight"] > 0]
    return result[INDEX_WEIGHT_COLUMNS]


def _coverage_summary(
    conn: duckdb.DuckDBPyConnection,
    index_code: str,
    start: date,
    end: date,
) -> Dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            count(*) AS rows,
            count(DISTINCT trade_date) AS dates,
            min(trade_date) AS coverage_start,
            max(trade_date) AS coverage_end,
            min(weight) AS min_weight,
            max(weight) AS max_weight
        FROM {INDEX_WEIGHT_TABLE}
        WHERE index_code = ? AND trade_date BETWEEN ? AND ?
        """,
        [index_code, start, end],
    ).fetchone()
    return {
        "rows": int(row[0] or 0),
        "dates": int(row[1] or 0),
        "coverage_start": str(row[2]) if row[2] else "",
        "coverage_end": str(row[3]) if row[3] else "",
        "min_weight": float(row[4] or 0.0),
        "max_weight": float(row[5] or 0.0),
    }


def _frame_coverage(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "dates": 0, "coverage_start": "", "coverage_end": "", "min_weight": 0.0, "max_weight": 0.0}
    return {
        "rows": int(len(frame)),
        "dates": int(frame["trade_date"].nunique()),
        "coverage_start": str(frame["trade_date"].min()),
        "coverage_end": str(frame["trade_date"].max()),
        "min_weight": float(frame["weight"].min()),
        "max_weight": float(frame["weight"].max()),
    }


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=INDEX_WEIGHT_COLUMNS)


def _date_chunks(start: date, end: date, chunk_days: int) -> Iterable[Tuple[date, date]]:
    current = start
    while current <= end:
        chunk_end = min(end, current + timedelta(days=chunk_days - 1))
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def _parse_date(value: str) -> date:
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _default_end_date() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")


def _fmt_tushare_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _from_ts_code(ts_code: str) -> str:
    return str(ts_code).split(".")[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=_default_end_date())
    parser.add_argument("--db-path", default=str(DEFAULT_WEIGHT_DB))
    parser.add_argument("--min-interval", type=float, default=0.25)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--chunk-days", type=int, default=3000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    summary = ingest_tushare_index_weight(
        index_code=args.index_code,
        weight_db_path=Path(args.db_path),
        start_date=args.start,
        end_date=args.end,
        min_interval=args.min_interval,
        timeout=args.timeout,
        chunk_days=args.chunk_days,
        dry_run=args.dry_run,
    )
    print(summary)


if __name__ == "__main__":
    main()
