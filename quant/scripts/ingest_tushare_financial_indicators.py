"""Ingest Tushare fina_indicator fields into a point-in-time sidecar DuckDB."""

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import duckdb
import pandas as pd

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR.parent))

from quant.scripts.ingest_tushare_daily_basic import TushareClient


logger = logging.getLogger("ingest_tushare_financial_indicators")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DUCKDB_DIR = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "live"
DEFAULT_MARKET_DB = DEFAULT_DUCKDB_DIR / "cn_ohlcv.duckdb"
DEFAULT_FINANCIAL_DB = DEFAULT_DUCKDB_DIR / "cn_financial_indicators.duckdb"
DAILY_CN_TABLE = "daily_cn_ochl"
FINANCIAL_TABLE = "cn_financial_indicators"

FINANCIAL_COLUMNS = [
    "symbol",
    "ts_code",
    "ann_date",
    "end_date",
    "roe",
    "roe_dt",
    "q_roe",
    "q_dt_roe",
    "roa",
    "grossprofit_margin",
    "netprofit_margin",
    "debt_to_assets",
    "current_ratio",
    "quick_ratio",
    "ocf_to_profit",
    "netprofit_yoy",
    "or_yoy",
    "op_yoy",
    "q_sales_yoy",
    "q_netprofit_yoy",
    "eps",
    "bps",
    "updated_at",
]

FINANCIAL_NUMERIC_COLUMNS = [
    "roe",
    "roe_dt",
    "q_roe",
    "q_dt_roe",
    "roa",
    "grossprofit_margin",
    "netprofit_margin",
    "debt_to_assets",
    "current_ratio",
    "quick_ratio",
    "ocf_to_profit",
    "netprofit_yoy",
    "or_yoy",
    "op_yoy",
    "q_sales_yoy",
    "q_netprofit_yoy",
    "eps",
    "bps",
]

FINANCIAL_FIELDS = ",".join([column for column in FINANCIAL_COLUMNS if column != "symbol" and column != "updated_at"])


@dataclass(frozen=True)
class FinancialIngestSummary:
    start: date
    end: date
    fetch_mode: str
    requested_items: int
    fetched_items: int
    fetched_rows: int
    skipped_items: int
    failed_items: int
    coverage: Dict[str, Any]


def ingest_tushare_financial_indicators(
    market_db_path: Path = DEFAULT_MARKET_DB,
    financial_db_path: Path = DEFAULT_FINANCIAL_DB,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    fetch_mode: str = "period",
    symbols: Optional[Sequence[str]] = None,
    min_interval: float = 0.25,
    timeout: int = 20,
    limit_items: Optional[int] = None,
    force: bool = False,
    apply_only: bool = False,
    dry_run: bool = False,
    client: Optional[Any] = None,
) -> FinancialIngestSummary:
    financial_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(financial_db_path))
    try:
        _ensure_financial_schema(conn)
        _attach_market_db(conn, market_db_path)
        start, end = _resolve_date_range(conn, start_date, end_date)
        market_symbols = _load_market_symbols(conn, start, end)
        allowed_symbols = set(market_symbols)

        if fetch_mode == "period":
            all_items = list(_periods_to_fetch(conn, list(_report_periods(start, end)), force=force))
        elif fetch_mode == "symbol":
            requested_symbols = [str(symbol).zfill(6) for symbol in symbols] if symbols else market_symbols
            all_items = list(_symbols_to_fetch(conn, requested_symbols, start, end, force=force))
        else:
            raise ValueError(f"unsupported fetch_mode: {fetch_mode}")

        requested_count = len(all_items)
        items = all_items[:limit_items] if limit_items is not None else all_items
        fetched_items = 0
        fetched_rows = 0
        failed_items = 0
        fetch_client = client
        if not dry_run and not apply_only:
            if fetch_client is None:
                fetch_client = TushareClient(min_interval=min_interval, timeout=timeout)
            for index, item in enumerate(items, start=1):
                try:
                    if fetch_mode == "period":
                        frame = fetch_financial_indicator_period(fetch_client, str(item), allowed_symbols=allowed_symbols)
                        inserted = _upsert_financial_indicators(conn, frame, end_date=_parse_tushare_date(str(item)))
                    else:
                        frame = fetch_financial_indicator_symbol(fetch_client, str(item), start, end)
                        inserted = _upsert_financial_indicators(conn, frame, symbol=str(item), start=start, end=end)
                    fetched_items += 1
                    fetched_rows += inserted
                    if index == 1 or index % 10 == 0 or index == len(items):
                        logger.info("Fetched %s/%s %s items (%s rows)", index, len(items), fetch_mode, fetched_rows)
                    if min_interval > 0:
                        time.sleep(min_interval)
                except Exception as exc:
                    failed_items += 1
                    logger.warning("Failed to fetch fina_indicator %s=%s: %s", fetch_mode, item, exc)

        coverage = _coverage_summary(conn, start, end, market_symbols)
        skipped_items = max(requested_count - len(items), 0)
        return FinancialIngestSummary(
            start=start,
            end=end,
            fetch_mode=fetch_mode,
            requested_items=requested_count,
            fetched_items=fetched_items,
            fetched_rows=fetched_rows,
            skipped_items=skipped_items,
            failed_items=failed_items,
            coverage=coverage,
        )
    finally:
        conn.close()


def fetch_financial_indicator_period(
    client: Any,
    period: str,
    allowed_symbols: Optional[set[str]] = None,
) -> pd.DataFrame:
    frame = client.call("fina_indicator", period=period, fields=FINANCIAL_FIELDS)
    return _normalize_financial_indicators(frame, allowed_symbols=allowed_symbols)


def fetch_financial_indicator_symbol(client: Any, symbol: str, start: date, end: date) -> pd.DataFrame:
    frame = client.call(
        "fina_indicator",
        ts_code=_ts_code(symbol),
        start_date=_fmt_date(start),
        end_date=_fmt_date(end),
        fields=FINANCIAL_FIELDS,
    )
    return _normalize_financial_indicators(frame)


def _ensure_financial_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {FINANCIAL_TABLE} (
            symbol VARCHAR,
            ts_code VARCHAR,
            ann_date DATE,
            end_date DATE,
            roe DOUBLE,
            roe_dt DOUBLE,
            q_roe DOUBLE,
            q_dt_roe DOUBLE,
            roa DOUBLE,
            grossprofit_margin DOUBLE,
            netprofit_margin DOUBLE,
            debt_to_assets DOUBLE,
            current_ratio DOUBLE,
            quick_ratio DOUBLE,
            ocf_to_profit DOUBLE,
            netprofit_yoy DOUBLE,
            or_yoy DOUBLE,
            op_yoy DOUBLE,
            q_sales_yoy DOUBLE,
            q_netprofit_yoy DOUBLE,
            eps DOUBLE,
            bps DOUBLE,
            updated_at TIMESTAMP
        )
        """
    )
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_{FINANCIAL_TABLE}_symbol_ann_end
        ON {FINANCIAL_TABLE}(symbol, ann_date, end_date)
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{FINANCIAL_TABLE}_symbol_ann ON {FINANCIAL_TABLE}(symbol, ann_date)")


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


def _load_market_symbols(conn: duckdb.DuckDBPyConnection, start: date, end: date) -> List[str]:
    rows = conn.execute(
        f"""
        SELECT DISTINCT symbol
        FROM market.{DAILY_CN_TABLE}
        WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
          AND regexp_matches(symbol, '^[0236][0-9]{{5}}$')
          AND NOT starts_with(symbol, '200')
        ORDER BY symbol
        """,
        [start, end],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _report_periods(start: date, end: date) -> Iterable[str]:
    suffixes = ("0331", "0630", "0930", "1231")
    for year in range(start.year, end.year + 1):
        for suffix in suffixes:
            period = f"{year}{suffix}"
            period_date = _parse_tushare_date(period)
            if start <= period_date <= end:
                yield period


def _periods_to_fetch(conn: duckdb.DuckDBPyConnection, periods: Sequence[str], force: bool = False) -> Iterable[str]:
    if force:
        yield from periods
        return
    for period in periods:
        period_date = _parse_tushare_date(period)
        row = conn.execute(f"SELECT COUNT(*) FROM {FINANCIAL_TABLE} WHERE end_date = ?", [period_date]).fetchone()
        if not row or int(row[0] or 0) == 0:
            yield period


def _symbols_to_fetch(
    conn: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
    start: date,
    end: date,
    force: bool = False,
) -> Iterable[str]:
    if force:
        yield from symbols
        return
    for symbol in symbols:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {FINANCIAL_TABLE}
            WHERE symbol = ?
              AND ann_date BETWEEN ? AND ?
            """,
            [symbol, start, end],
        ).fetchone()
        if not row or int(row[0] or 0) == 0:
            yield symbol


def _normalize_financial_indicators(
    frame: pd.DataFrame,
    allowed_symbols: Optional[set[str]] = None,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=FINANCIAL_COLUMNS)
    data = frame.copy()
    if "ts_code" not in data.columns or "ann_date" not in data.columns or "end_date" not in data.columns:
        raise ValueError("fina_indicator response missing ts_code, ann_date, or end_date")
    data["symbol"] = data["ts_code"].map(_symbol_from_ts_code)
    data["ann_date"] = pd.to_datetime(data["ann_date"], format="%Y%m%d", errors="coerce").dt.date
    data["end_date"] = pd.to_datetime(data["end_date"], format="%Y%m%d", errors="coerce").dt.date
    if allowed_symbols is not None:
        data = data[data["symbol"].isin(allowed_symbols)]
    for column in FINANCIAL_NUMERIC_COLUMNS:
        if column not in data.columns:
            data[column] = pd.NA
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["updated_at"] = pd.Timestamp.now(tz="UTC").tz_localize(None)
    data = data.dropna(subset=["symbol", "ann_date", "end_date"])
    data = data.drop_duplicates(subset=["symbol", "ann_date", "end_date"], keep="last")
    for column in FINANCIAL_COLUMNS:
        if column not in data.columns:
            data[column] = pd.NA
    return data[FINANCIAL_COLUMNS].sort_values(["symbol", "ann_date", "end_date"]).reset_index(drop=True)


def _upsert_financial_indicators(
    conn: duckdb.DuckDBPyConnection,
    frame: pd.DataFrame,
    end_date: Optional[date] = None,
    symbol: Optional[str] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> int:
    if end_date is not None:
        conn.execute(f"DELETE FROM {FINANCIAL_TABLE} WHERE end_date = ?", [end_date])
    elif symbol is not None and start is not None and end is not None:
        conn.execute(
            f"""
            DELETE FROM {FINANCIAL_TABLE}
            WHERE symbol = ?
              AND ann_date BETWEEN ? AND ?
            """,
            [symbol, start, end],
        )
    if frame is None or frame.empty:
        return 0
    values = frame[FINANCIAL_COLUMNS].copy()
    conn.register("stage_financial_indicators", values)
    try:
        columns = ", ".join(FINANCIAL_COLUMNS)
        conn.execute(f"INSERT INTO {FINANCIAL_TABLE} ({columns}) SELECT {columns} FROM stage_financial_indicators")
    finally:
        conn.unregister("stage_financial_indicators")
    return len(values)


def _coverage_summary(
    conn: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    market_symbols: Sequence[str],
) -> Dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS rows,
            COUNT(DISTINCT symbol) AS symbols,
            MIN(ann_date) AS ann_start,
            MAX(ann_date) AS ann_end,
            MIN(end_date) AS period_start,
            MAX(end_date) AS period_end,
            COUNT(roe) AS roe_rows,
            COUNT(netprofit_yoy) AS netprofit_yoy_rows,
            COUNT(debt_to_assets) AS debt_to_assets_rows
        FROM {FINANCIAL_TABLE}
        WHERE end_date BETWEEN ? AND ?
        """,
        [start, end],
    ).fetchone()
    total_rows = int(row[0] or 0)
    return {
        "market_symbols": len(market_symbols),
        "indicator_rows": total_rows,
        "indicator_symbols": int(row[1] or 0),
        "ann_start": row[2],
        "ann_end": row[3],
        "period_start": row[4],
        "period_end": row[5],
        "roe_rows": int(row[6] or 0),
        "netprofit_yoy_rows": int(row[7] or 0),
        "debt_to_assets_rows": int(row[8] or 0),
        "roe_coverage": (int(row[6] or 0) / total_rows) if total_rows else 0.0,
    }


def _parse_date(value: str) -> date:
    return pd.Timestamp(value).date()


def _parse_tushare_date(value: str) -> date:
    return pd.to_datetime(value, format="%Y%m%d", errors="raise").date()


def _fmt_date(value: date) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def _symbol_from_ts_code(value: Any) -> str:
    return str(value).split(".")[0].zfill(6)


def _ts_code(symbol: str) -> str:
    value = str(symbol).split(".")[0].zfill(6)
    if value.startswith("6"):
        return f"{value}.SH"
    if value.startswith(("0", "2", "3")):
        return f"{value}.SZ"
    if value.startswith(("4", "8", "9")):
        return f"{value}.BJ"
    return value


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _print_summary(summary: FinancialIngestSummary) -> None:
    logger.info(
        "Summary start=%s end=%s mode=%s requested_items=%s fetched_items=%s fetched_rows=%s skipped_items=%s failed_items=%s",
        summary.start,
        summary.end,
        summary.fetch_mode,
        summary.requested_items,
        summary.fetched_items,
        summary.fetched_rows,
        summary.skipped_items,
        summary.failed_items,
    )
    logger.info("Coverage: %s", summary.coverage)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest Tushare fina_indicator into a PIT sidecar DuckDB")
    parser.add_argument("--market-db-path", default=str(DEFAULT_MARKET_DB), help="Path to cn_ohlcv.duckdb")
    parser.add_argument("--financial-db-path", default=str(DEFAULT_FINANCIAL_DB), help="Path to cn_financial_indicators.duckdb")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD, default local DB min date")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD, default local DB max date")
    parser.add_argument("--fetch-mode", choices=["period", "symbol"], default="period", help="Fetch by report period or by symbol")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols for --fetch-mode symbol")
    parser.add_argument("--min-interval", type=float, default=0.25, help="Minimum seconds between Tushare calls")
    parser.add_argument("--timeout", type=int, default=20, help="Per-request Tushare timeout in seconds")
    parser.add_argument("--limit-items", type=int, default=None, help="Fetch at most this many periods or symbols")
    parser.add_argument("--force", action="store_true", help="Refetch periods/symbols even if the sidecar already has rows")
    parser.add_argument("--apply-only", action="store_true", help="Only summarize existing sidecar data")
    parser.add_argument("--dry-run", action="store_true", help="Resolve fetch items without calling Tushare")
    args = parser.parse_args(argv)

    _configure_logging()
    symbol_list = [item.strip() for item in args.symbols.split(",") if item.strip()] if args.symbols else None
    summary = ingest_tushare_financial_indicators(
        market_db_path=Path(args.market_db_path),
        financial_db_path=Path(args.financial_db_path),
        start_date=args.start,
        end_date=args.end,
        fetch_mode=args.fetch_mode,
        symbols=symbol_list,
        min_interval=args.min_interval,
        timeout=args.timeout,
        limit_items=args.limit_items,
        force=args.force,
        apply_only=args.apply_only,
        dry_run=args.dry_run,
    )
    _print_summary(summary)


if __name__ == "__main__":
    main()
