"""Ingest Tushare daily_basic fields into a sidecar DuckDB."""

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

import duckdb
import pandas as pd

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR.parent))

from quant.shared.utils.config_loader import ConfigLoader

try:
    import tushare as ts
except ImportError:
    ts = None


logger = logging.getLogger("ingest_tushare_daily_basic")
_THREAD_LOCAL = threading.local()
_RATE_LIMIT_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DUCKDB_DIR = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "live"
DEFAULT_MARKET_DB = DEFAULT_DUCKDB_DIR / "cn_ohlcv.duckdb"
DEFAULT_BASIC_DB = DEFAULT_DUCKDB_DIR / "cn_daily_basic.duckdb"
DAILY_CN_TABLE = "daily_cn_ochl"
BASIC_TABLE = "cn_daily_basic"

BASIC_COLUMNS = [
    "trade_date",
    "symbol",
    "ts_code",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
    "updated_at",
]

BASIC_NUMERIC_COLUMNS = [
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
]

DAILY_BASIC_FIELDS = ",".join(
    [
        "ts_code",
        "trade_date",
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
    ]
)


@dataclass(frozen=True)
class IngestSummary:
    start: date
    end: date
    requested_dates: int
    fetched_dates: int
    fetched_rows: int
    skipped_dates: int
    failed_dates: int
    coverage: Dict[str, Any]


class TushareClient:
    def __init__(self, min_interval: float = 0.25, retries: int = 3, timeout: int = 10):
        if ts is None:
            raise RuntimeError("tushare is not installed")
        cfg = ConfigLoader().load("config.yaml")
        tushare_cfg = cfg.get("data", {}).get("tushare", {})
        token = tushare_cfg.get("token", "")
        api_url = tushare_cfg.get("api_url", "")
        if not token:
            raise RuntimeError("tushare token is not configured")
        self.api = ts.pro_api(token=token, timeout=timeout)
        if api_url:
            self.api._DataApi__http_url = api_url
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self._last_request = 0.0

    def call(self, name: str, **kwargs: Any) -> pd.DataFrame:
        last_error: Optional[Exception] = None
        for attempt in range(self.retries):
            self._rate_limit()
            try:
                frame = getattr(self.api, name)(**kwargs)
                if frame is None:
                    return pd.DataFrame()
                return frame
            except Exception as exc:
                last_error = exc
                sleep_s = min(2.0 * (attempt + 1), 10.0)
                logger.warning("Tushare %s failed on attempt %s/%s: %s", name, attempt + 1, self.retries, exc)
                time.sleep(sleep_s)
        raise RuntimeError(f"Tushare {name} failed") from last_error

    def _rate_limit(self) -> None:
        global _LAST_REQUEST_AT
        with _RATE_LIMIT_LOCK:
            elapsed = time.time() - _LAST_REQUEST_AT
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            _LAST_REQUEST_AT = time.time()
        self._last_request = _LAST_REQUEST_AT


def ingest_tushare_daily_basic(
    market_db_path: Path = DEFAULT_MARKET_DB,
    basic_db_path: Path = DEFAULT_BASIC_DB,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_interval: float = 0.25,
    timeout: int = 10,
    workers: int = 1,
    limit_dates: Optional[int] = None,
    force: bool = False,
    apply_only: bool = False,
    dry_run: bool = False,
) -> IngestSummary:
    basic_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(basic_db_path))
    try:
        _ensure_basic_schema(conn)
        _attach_market_db(conn, market_db_path)
        start, end = _resolve_date_range(conn, start_date, end_date)
        dates = list(_dates_to_fetch(conn, start, end, force=force))
        requested_count = len(dates)
        if limit_dates is not None:
            dates = dates[:limit_dates]
        logger.info("Resolved %s to %s with %s fetch dates (%s before limit)", start, end, len(dates), requested_count)

        fetched_dates = 0
        fetched_rows = 0
        failed_dates = 0
        if not dry_run and not apply_only:
            if workers <= 1:
                client = TushareClient(min_interval=min_interval, timeout=timeout)
                for idx, trade_date in enumerate(dates, start=1):
                    try:
                        frame = fetch_daily_basic(client, trade_date)
                        inserted = _upsert_daily_basic(conn, frame, trade_date)
                        fetched_dates += 1
                        fetched_rows += inserted
                        if idx == 1 or idx % 25 == 0 or idx == len(dates):
                            logger.info("Fetched %s/%s dates through %s (%s rows)", idx, len(dates), trade_date, fetched_rows)
                    except Exception as exc:
                        failed_dates += 1
                        logger.warning("Failed to fetch daily_basic for %s: %s", trade_date, exc)
            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(_fetch_daily_basic_worker, trade_date, min_interval, timeout): trade_date
                        for trade_date in dates
                    }
                    for idx, future in enumerate(as_completed(futures), start=1):
                        trade_date = futures[future]
                        try:
                            frame = future.result()
                            inserted = _upsert_daily_basic(conn, frame, trade_date)
                            fetched_dates += 1
                            fetched_rows += inserted
                            if idx == 1 or idx % 25 == 0 or idx == len(dates):
                                logger.info("Fetched %s/%s dates; latest completed %s (%s rows)", idx, len(dates), trade_date, fetched_rows)
                        except Exception as exc:
                            failed_dates += 1
                            logger.warning("Failed to fetch daily_basic for %s: %s", trade_date, exc)

        coverage = _coverage_summary(conn, start, end)
        skipped_dates = max(requested_count - len(dates), 0)
        return IngestSummary(start, end, requested_count, fetched_dates, fetched_rows, skipped_dates, failed_dates, coverage)
    finally:
        conn.close()


def fetch_daily_basic(client: TushareClient, trade_date: date) -> pd.DataFrame:
    frame = client.call("daily_basic", trade_date=_fmt_date(trade_date), fields=DAILY_BASIC_FIELDS)
    return _normalize_daily_basic(frame)


def _fetch_daily_basic_worker(trade_date: date, min_interval: float, timeout: int) -> pd.DataFrame:
    client = getattr(_THREAD_LOCAL, "client", None)
    if client is None or client.min_interval != min_interval or client.timeout != timeout:
        client = TushareClient(min_interval=min_interval, timeout=timeout)
        _THREAD_LOCAL.client = client
    return fetch_daily_basic(client, trade_date)


def _ensure_basic_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {BASIC_TABLE} (
            trade_date DATE,
            symbol VARCHAR,
            ts_code VARCHAR,
            turnover_rate DOUBLE,
            turnover_rate_f DOUBLE,
            volume_ratio DOUBLE,
            pe DOUBLE,
            pe_ttm DOUBLE,
            pb DOUBLE,
            ps DOUBLE,
            ps_ttm DOUBLE,
            dv_ratio DOUBLE,
            dv_ttm DOUBLE,
            total_share DOUBLE,
            float_share DOUBLE,
            free_share DOUBLE,
            total_mv DOUBLE,
            circ_mv DOUBLE,
            updated_at TIMESTAMP
        )
        """
    )
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{BASIC_TABLE}_symbol_date
        ON {BASIC_TABLE}(symbol, trade_date)
        """
    )


def _attach_market_db(conn: duckdb.DuckDBPyConnection, market_db_path: Path) -> None:
    if not market_db_path.exists():
        raise RuntimeError(f"market DB not found: {market_db_path}")
    attached = {
        row[1]
        for row in conn.execute("PRAGMA database_list").fetchall()
        if len(row) > 1
    }
    if "market" not in attached:
        path = str(market_db_path).replace("'", "''")
        conn.execute(f"ATTACH IF NOT EXISTS '{path}' AS market (READ_ONLY)")
    exists = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_catalog = 'market'
          AND table_name = ?
        """,
        [DAILY_CN_TABLE],
    ).fetchone()[0]
    if not exists:
        raise RuntimeError(f"market DB does not contain {DAILY_CN_TABLE}")


def _resolve_date_range(
    conn: duckdb.DuckDBPyConnection,
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[date, date]:
    row = conn.execute(
        f"""
        SELECT MIN(CAST(timestamp AS DATE)), MAX(CAST(timestamp AS DATE))
        FROM market.{DAILY_CN_TABLE}
        """
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        raise RuntimeError(f"market.{DAILY_CN_TABLE} has no rows")
    start = _parse_date(start_date) if start_date else row[0]
    end = _parse_date(end_date) if end_date else row[1]
    if start > end:
        raise ValueError(f"start date {start} is after end date {end}")
    return start, end


def _dates_to_fetch(
    conn: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    force: bool = False,
) -> Iterable[date]:
    if force:
        rows = conn.execute(
            f"""
            SELECT DISTINCT CAST(timestamp AS DATE) AS trade_date
            FROM market.{DAILY_CN_TABLE}
            WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
            ORDER BY trade_date
            """,
            [start, end],
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            WITH local_dates AS (
                SELECT DISTINCT CAST(timestamp AS DATE) AS trade_date
                FROM market.{DAILY_CN_TABLE}
                WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
            )
            SELECT d.trade_date
            FROM local_dates d
            WHERE NOT EXISTS (
                SELECT 1
                FROM {BASIC_TABLE} b
                WHERE b.trade_date = d.trade_date
                  AND (b.total_mv IS NOT NULL OR b.circ_mv IS NOT NULL)
                LIMIT 1
            )
            ORDER BY d.trade_date
            """,
            [start, end],
        ).fetchall()
    for row in rows:
        yield row[0]


def _normalize_daily_basic(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=BASIC_COLUMNS)
    data = frame.copy()
    if "trade_date" not in data.columns or "ts_code" not in data.columns:
        raise ValueError("daily_basic response missing trade_date or ts_code")
    data["trade_date"] = pd.to_datetime(data["trade_date"], format="%Y%m%d", errors="coerce").dt.date
    data["symbol"] = data["ts_code"].map(_symbol_from_ts_code)
    for column in BASIC_NUMERIC_COLUMNS:
        if column not in data.columns:
            data[column] = pd.NA
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["updated_at"] = pd.Timestamp.now(tz="UTC").tz_localize(None)
    data = data.dropna(subset=["trade_date", "symbol"])
    for column in BASIC_COLUMNS:
        if column not in data.columns:
            data[column] = pd.NA
    return data[BASIC_COLUMNS].sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _upsert_daily_basic(conn: duckdb.DuckDBPyConnection, frame: pd.DataFrame, trade_date: date) -> int:
    conn.execute(f"DELETE FROM {BASIC_TABLE} WHERE trade_date = ?", [trade_date])
    if frame is None or frame.empty:
        return 0
    values = frame[BASIC_COLUMNS].copy()
    conn.register("stage_daily_basic", values)
    try:
        columns = ", ".join(BASIC_COLUMNS)
        conn.execute(f"INSERT INTO {BASIC_TABLE} ({columns}) SELECT {columns} FROM stage_daily_basic")
    finally:
        conn.unregister("stage_daily_basic")
    return len(values)


def _coverage_summary(conn: duckdb.DuckDBPyConnection, start: date, end: date) -> Dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS bar_rows,
            COUNT(b.total_mv) AS total_mv_rows,
            COUNT(b.circ_mv) AS circ_mv_rows,
            COUNT(DISTINCT d.symbol) AS symbols,
            COUNT(DISTINCT CASE WHEN b.total_mv IS NOT NULL OR b.circ_mv IS NOT NULL THEN d.symbol END) AS cap_symbols,
            MIN(CASE WHEN b.total_mv IS NOT NULL OR b.circ_mv IS NOT NULL THEN CAST(d.timestamp AS DATE) END) AS cap_start,
            MAX(CASE WHEN b.total_mv IS NOT NULL OR b.circ_mv IS NOT NULL THEN CAST(d.timestamp AS DATE) END) AS cap_end
        FROM market.{DAILY_CN_TABLE} d
        LEFT JOIN {BASIC_TABLE} b
          ON d.symbol = b.symbol
         AND CAST(d.timestamp AS DATE) = b.trade_date
        WHERE CAST(d.timestamp AS DATE) BETWEEN ? AND ?
        """,
        [start, end],
    ).fetchone()
    basic_row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS basic_rows,
            COUNT(DISTINCT trade_date) AS basic_dates,
            MIN(trade_date) AS basic_start,
            MAX(trade_date) AS basic_end
        FROM {BASIC_TABLE}
        WHERE trade_date BETWEEN ? AND ?
        """,
        [start, end],
    ).fetchone()
    bar_rows = int(row[0] or 0)
    total_mv_rows = int(row[1] or 0)
    circ_mv_rows = int(row[2] or 0)
    return {
        "bar_rows": bar_rows,
        "total_mv_rows": total_mv_rows,
        "circ_mv_rows": circ_mv_rows,
        "total_mv_coverage": (total_mv_rows / bar_rows) if bar_rows else 0.0,
        "circ_mv_coverage": (circ_mv_rows / bar_rows) if bar_rows else 0.0,
        "symbols": int(row[3] or 0),
        "cap_symbols": int(row[4] or 0),
        "cap_start": row[5],
        "cap_end": row[6],
        "basic_rows": int(basic_row[0] or 0),
        "basic_dates": int(basic_row[1] or 0),
        "basic_start": basic_row[2],
        "basic_end": basic_row[3],
    }


def _parse_date(value: str) -> date:
    return pd.Timestamp(value).date()


def _fmt_date(value: date) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def _symbol_from_ts_code(value: Any) -> str:
    return str(value).split(".")[0].zfill(6)


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _print_summary(summary: IngestSummary) -> None:
    logger.info(
        "Summary start=%s end=%s requested_dates=%s fetched_dates=%s fetched_rows=%s skipped_dates=%s failed_dates=%s",
        summary.start,
        summary.end,
        summary.requested_dates,
        summary.fetched_dates,
        summary.fetched_rows,
        summary.skipped_dates,
        summary.failed_dates,
    )
    logger.info("Coverage: %s", summary.coverage)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest Tushare daily_basic into a sidecar DuckDB")
    parser.add_argument("--market-db-path", default=str(DEFAULT_MARKET_DB), help="Path to cn_ohlcv.duckdb")
    parser.add_argument("--basic-db-path", default=str(DEFAULT_BASIC_DB), help="Path to cn_daily_basic.duckdb")
    parser.add_argument("--db-path", default=None, help="Deprecated alias for --market-db-path")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD, default local DB min date")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD, default local DB max date")
    parser.add_argument("--min-interval", type=float, default=0.25, help="Minimum seconds between Tushare calls")
    parser.add_argument("--timeout", type=int, default=10, help="Per-request Tushare timeout in seconds")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent Tushare fetch workers")
    parser.add_argument("--limit-dates", type=int, default=None, help="Fetch at most this many missing trade dates")
    parser.add_argument("--force", action="store_true", help="Refetch dates even if cn_daily_basic already has rows")
    parser.add_argument("--apply-only", action="store_true", help="Only summarize existing sidecar data")
    parser.add_argument("--dry-run", action="store_true", help="Resolve dates without calling Tushare")
    args = parser.parse_args(argv)

    _configure_logging()
    market_db_path = Path(args.db_path or args.market_db_path)
    summary = ingest_tushare_daily_basic(
        market_db_path=market_db_path,
        basic_db_path=Path(args.basic_db_path),
        start_date=args.start,
        end_date=args.end,
        min_interval=args.min_interval,
        timeout=args.timeout,
        workers=max(1, args.workers),
        limit_dates=args.limit_dates,
        force=args.force,
        apply_only=args.apply_only,
        dry_run=args.dry_run,
    )
    _print_summary(summary)


if __name__ == "__main__":
    main()
