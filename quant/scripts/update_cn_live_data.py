"""Update all live CN DuckDB sidecars to a target date."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import duckdb
import pandas as pd

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR.parent))

from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.parquet_lake_storage import ParquetLakeStorage
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.scripts.build_cn_security_status import build_cn_security_status
from quant.scripts.ingest_tushare_daily_basic import ingest_tushare_daily_basic
from quant.scripts.ingest_tushare_financial_indicators import ingest_tushare_financial_indicators
from quant.scripts.ingest_tushare_index_weight import ingest_tushare_index_weight


logger = logging.getLogger("update_cn_live_data")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DUCKDB_DIR = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "live"
DEFAULT_STOCK_DB = DEFAULT_DUCKDB_DIR / "cn_ohlcv.duckdb"
DEFAULT_ETF_DB = DEFAULT_DUCKDB_DIR / "cn_etf_ohlcv.duckdb"
DEFAULT_INDEX_DB = DEFAULT_DUCKDB_DIR / "cn_index_ohlcv.duckdb"
DEFAULT_DAILY_BASIC_DB = DEFAULT_DUCKDB_DIR / "cn_daily_basic.duckdb"
DEFAULT_FINANCIAL_DB = DEFAULT_DUCKDB_DIR / "cn_financial_indicators.duckdb"
DEFAULT_STATUS_DB = DEFAULT_DUCKDB_DIR / "cn_status.duckdb"
DEFAULT_INDEX_WEIGHT_DB = DEFAULT_DUCKDB_DIR / "cn_index_weight.duckdb"
DEFAULT_FUND_NAV_DB = DEFAULT_DUCKDB_DIR / "cn_fund_nav.duckdb"
DEFAULT_CORPORATE_ACTIONS_DB = DEFAULT_DUCKDB_DIR / "cn_corporate_actions.duckdb"
DEFAULT_PARQUET_LAKE_ROOT = ROOT / "quant" / "infrastructure" / "var" / "parquet_lake"
DEFAULT_PARQUET_LAKE_REMOTE_PREFIX = "oss:quant-duckdb-backup/vk-quant/parquet-lake"
DEFAULT_MAJOR_INDICES = ("000001", "000016", "000300", "000905", "399001", "399006", "399673")
DEFAULT_INDEX_WEIGHT_CODES = ("000300.SH",)


@dataclass(frozen=True)
class SymbolRange:
    symbol: str
    start: Optional[date]
    end: Optional[date]
    rows: int


@dataclass(frozen=True)
class StepSummary:
    name: str
    requested: int
    updated: int
    skipped: int
    failed: int
    rows: int
    start: Optional[date]
    end: date


class _NoopProviderStorage:
    def close(self) -> None:
        return None


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _default_target_date() -> date:
    return date.today() - timedelta(days=1)


def _fmt_date(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def _load_ranges(db_path: Path, table: str, date_column: str) -> Dict[str, SymbolRange]:
    if not db_path.exists():
        return {}
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        exists = conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = ?
            """,
            [table],
        ).fetchone()[0]
        if not exists:
            return {}
        rows = conn.execute(
            f"""
            SELECT
                symbol,
                MIN(CAST({date_column} AS DATE)) AS start_date,
                MAX(CAST({date_column} AS DATE)) AS end_date,
                COUNT(*) AS rows
            FROM {table}
            GROUP BY symbol
            ORDER BY symbol
            """
        ).fetchall()
    finally:
        conn.close()
    return {
        str(symbol): SymbolRange(str(symbol), start, end, int(count or 0))
        for symbol, start, end, count in rows
    }


def _load_table_max_date(db_path: Path, table: str, date_column: str) -> Optional[date]:
    if not db_path.exists():
        return None
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        row = conn.execute(f"SELECT MAX(CAST({date_column} AS DATE)) FROM {table}").fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    return row[0] if row and row[0] else None


def _load_latest_adj_factors(db_path: Path, table: str) -> Dict[str, float]:
    if not db_path.exists():
        return {}
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute(
            f"""
            SELECT symbol, adj_factor
            FROM (
                SELECT
                    symbol,
                    adj_factor,
                    ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp DESC) AS rn
                FROM {table}
                WHERE adj_factor IS NOT NULL
            )
            WHERE rn = 1
            """
        ).fetchall()
    except Exception:
        return {}
    finally:
        conn.close()
    return {str(symbol): float(adj_factor) for symbol, adj_factor in rows if adj_factor is not None}


def _lake_ranges(storage: ParquetLakeStorage, dataset: str) -> Dict[str, SymbolRange]:
    return {
        symbol: SymbolRange(symbol, start, end, rows)
        for symbol, (start, end, rows) in storage.load_ranges(dataset).items()
    }


def _lake_sidecar_start(storage: ParquetLakeStorage, dataset: str, target_end: date) -> Optional[date]:
    ranges = _lake_ranges(storage, dataset)
    max_date = max((item.end for item in ranges.values() if item.end is not None), default=None)
    if max_date is None:
        return None
    candidate = max_date + timedelta(days=1)
    if candidate > target_end:
        return None
    return candidate


def _copy_duckdb_range_to_lake(
    storage: ParquetLakeStorage,
    db_path: Path,
    table: str,
    dataset: str,
    date_column: str,
    start: Optional[date],
    end: date,
) -> int:
    if start is None or not db_path.exists():
        return 0
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        exists = conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = ?
            """,
            [table],
        ).fetchone()[0]
        if not exists:
            return 0
        frame = conn.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE CAST({date_column} AS DATE) >= ?
              AND CAST({date_column} AS DATE) <= ?
            """,
            [start, end],
        ).fetchdf()
    finally:
        conn.close()
    if frame.empty:
        return 0
    return storage.write_frame(dataset, frame)


def _next_start(
    current_end: Optional[date],
    target_end: date,
    explicit_start: Optional[date],
    full_start: date,
    overlap_days: int,
) -> Optional[date]:
    if explicit_start is not None:
        start = explicit_start
    elif current_end is None:
        start = full_start
    elif overlap_days > 0:
        start = current_end - timedelta(days=overlap_days - 1)
    else:
        start = current_end + timedelta(days=1)
    if start > target_end:
        return None
    return start


def _limit_items(items: Sequence[str], limit: int) -> List[str]:
    values = list(items)
    return values[:limit] if limit > 0 else values


def _fetch_open_trade_dates(provider: TushareProvider, start: date, end: date) -> List[date]:
    provider._rate_limit()
    frame = provider._api.trade_cal(
        exchange="SSE",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        fields="cal_date,is_open",
    )
    if frame is None or frame.empty:
        return []
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["cal_date"], format="%Y%m%d", errors="coerce").dt.date
    frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce").fillna(0).astype(int)
    frame = frame[
        (frame["is_open"] == 1)
        & (frame["trade_date"] >= start)
        & (frame["trade_date"] <= end)
    ]
    return sorted(frame["trade_date"].dropna().unique().tolist())


def _normalize_stock_daily_by_date(daily: pd.DataFrame, adj: pd.DataFrame, allowed_symbols: set[str]) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()
    frame = daily.copy()
    frame["symbol"] = frame["ts_code"].astype(str).str.split(".").str[0]
    frame = frame[frame["symbol"].isin(allowed_symbols)]
    if frame.empty:
        return pd.DataFrame()
    frame = frame.rename(
        columns={
            "trade_date": "timestamp",
            "vol": "volume",
            "amount": "turnover",
        }
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], format="%Y%m%d", errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if adj is not None and not adj.empty:
        factors = adj.copy()
        factors["symbol"] = factors["ts_code"].astype(str).str.split(".").str[0]
        factors["timestamp"] = pd.to_datetime(factors["trade_date"], format="%Y%m%d", errors="coerce")
        factors["adj_factor"] = pd.to_numeric(factors["adj_factor"], errors="coerce")
        frame = frame.merge(factors[["timestamp", "symbol", "adj_factor"]], on=["timestamp", "symbol"], how="left")
    if "adj_factor" not in frame.columns:
        frame["adj_factor"] = 1.0
    frame["adj_factor"] = frame["adj_factor"].fillna(1.0)
    for column in ("open", "high", "low", "close"):
        frame[f"adj_{column}"] = frame[column] * frame["adj_factor"]
    columns = [
        "timestamp",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "adj_factor",
    ]
    return frame[columns].dropna(subset=["timestamp", "symbol", "open", "high", "low", "close"])


def _normalize_fund_daily_by_date(
    daily: pd.DataFrame,
    allowed_symbols: set[str],
    latest_adj_factors: Dict[str, float],
) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()
    frame = daily.copy()
    frame["symbol"] = frame["ts_code"].astype(str).str.split(".").str[0]
    frame = frame[frame["symbol"].isin(allowed_symbols)]
    if frame.empty:
        return pd.DataFrame()
    frame = frame.rename(
        columns={
            "trade_date": "timestamp",
            "vol": "volume",
            "amount": "turnover",
        }
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], format="%Y%m%d", errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["adj_factor"] = frame["symbol"].map(latest_adj_factors).fillna(1.0)
    for column in ("open", "high", "low", "close"):
        frame[f"adj_{column}"] = frame[column] * frame["adj_factor"]
    columns = [
        "timestamp",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "adj_factor",
    ]
    return frame[columns].dropna(subset=["timestamp", "symbol", "open", "high", "low", "close"])


def _update_stock_bars_by_date(
    provider: TushareProvider,
    storage: DuckDBStorage,
    ranges: Dict[str, SymbolRange],
    target_end: date,
    explicit_start: Optional[date],
    full_start: date,
    limit: int,
) -> StepSummary:
    symbols = _limit_items(sorted(ranges), limit)
    if not symbols:
        return StepSummary("stocks", 0, 0, 0, 0, 0, None, target_end)
    max_end = max((item.end for item in ranges.values() if item.end is not None), default=None)
    start = explicit_start or _next_start(max_end, target_end, None, full_start, 0)
    if start is None:
        return StepSummary("stocks", len(symbols), 0, len(symbols), 0, 0, None, target_end)
    trade_dates = _fetch_open_trade_dates(provider, start, target_end)
    allowed = set(symbols)
    updated = skipped = failed = rows_saved = 0
    for index, trade_date in enumerate(trade_dates, start=1):
        try:
            provider._rate_limit()
            daily = provider._api.daily(
                trade_date=trade_date.strftime("%Y%m%d"),
                fields="ts_code,trade_date,open,high,low,close,vol,amount",
            )
            provider._rate_limit()
            adj = provider._api.adj_factor(
                trade_date=trade_date.strftime("%Y%m%d"),
                fields="ts_code,trade_date,adj_factor",
            )
            frame = _normalize_stock_daily_by_date(daily, adj, allowed)
            if frame.empty:
                skipped += 1
            else:
                rows_saved += storage.save_bars(frame, timeframe="1d")
                updated += 1
        except Exception as exc:
            failed += 1
            logger.warning("stock date %s failed: %s", trade_date, exc)
        if index == 1 or index % 10 == 0 or index == len(trade_dates):
            logger.info("stock dates %s/%s updated=%s skipped=%s failed=%s rows=%s", index, len(trade_dates), updated, skipped, failed, rows_saved)
    return StepSummary("stocks", len(trade_dates), updated, skipped, failed, rows_saved, start, target_end)


def _update_etf_bars_by_date(
    provider: TushareProvider,
    storage: DuckDBStorage,
    ranges: Dict[str, SymbolRange],
    latest_adj_factors: Dict[str, float],
    target_end: date,
    explicit_start: Optional[date],
    full_start: date,
    limit: int,
) -> StepSummary:
    symbols = _limit_items(sorted(ranges), limit)
    if not symbols:
        return StepSummary("etfs", 0, 0, 0, 0, 0, None, target_end)
    max_end = max((item.end for item in ranges.values() if item.end is not None), default=None)
    start = explicit_start or _next_start(max_end, target_end, None, full_start, 0)
    if start is None:
        return StepSummary("etfs", len(symbols), 0, len(symbols), 0, 0, None, target_end)
    trade_dates = _fetch_open_trade_dates(provider, start, target_end)
    allowed = set(symbols)
    updated = skipped = failed = rows_saved = 0
    for index, trade_date in enumerate(trade_dates, start=1):
        try:
            provider._rate_limit()
            daily = provider._api.fund_daily(
                trade_date=trade_date.strftime("%Y%m%d"),
                fields="ts_code,trade_date,open,high,low,close,vol,amount",
            )
            frame = _normalize_fund_daily_by_date(daily, allowed, latest_adj_factors)
            if frame.empty:
                skipped += 1
            else:
                rows_saved += storage.save_bars(frame, timeframe="1d")
                updated += 1
        except Exception as exc:
            failed += 1
            logger.warning("etf date %s failed: %s", trade_date, exc)
        if index == 1 or index % 10 == 0 or index == len(trade_dates):
            logger.info("etf dates %s/%s updated=%s skipped=%s failed=%s rows=%s", index, len(trade_dates), updated, skipped, failed, rows_saved)
    return StepSummary("etfs", len(trade_dates), updated, skipped, failed, rows_saved, start, target_end)


def _update_fund_nav_by_symbol(
    provider: TushareProvider,
    storage: DuckDBStorage,
    ranges: Dict[str, SymbolRange],
    target_end: date,
    explicit_start: Optional[date],
    full_start: date,
    overlap_days: int,
    limit: int,
) -> StepSummary:
    symbols = _limit_items(sorted(ranges), limit)
    updated = skipped = failed = rows_saved = 0
    first_start: Optional[date] = None
    for index, symbol in enumerate(symbols, start=1):
        symbol_start = _next_start(
            ranges[symbol].end,
            target_end,
            explicit_start,
            full_start,
            overlap_days,
        )
        if symbol_start is None:
            skipped += 1
            continue
        first_start = min(first_start, symbol_start) if first_start else symbol_start
        try:
            nav = provider.fetch_fund_nav(
                symbol,
                datetime.combine(symbol_start, datetime.min.time()),
                datetime.combine(target_end, datetime.min.time()),
            )
            if nav.empty:
                skipped += 1
            else:
                rows_saved += storage.save_cn_fund_nav(nav)
                updated += 1
        except Exception as exc:
            failed += 1
            logger.warning("fund nav %s failed: %s", symbol, exc)
        if index == 1 or index % 50 == 0 or index == len(symbols):
            logger.info("fund nav %s/%s updated=%s skipped=%s failed=%s rows=%s", index, len(symbols), updated, skipped, failed, rows_saved)
    return StepSummary("fund_nav", len(symbols), updated, skipped, failed, rows_saved, first_start, target_end)


def _update_stock_bars(
    provider: TushareProvider,
    storage: DuckDBStorage,
    ranges: Dict[str, SymbolRange],
    target_end: date,
    explicit_start: Optional[date],
    full_start: date,
    overlap_days: int,
    limit: int,
    refresh_dividends: bool,
) -> StepSummary:
    symbols = _limit_items(sorted(ranges), limit)
    updated = skipped = failed = rows_saved = 0
    first_start: Optional[date] = None
    for index, symbol in enumerate(symbols, start=1):
        symbol_start = _next_start(
            ranges[symbol].end,
            target_end,
            explicit_start,
            full_start,
            overlap_days,
        )
        if symbol_start is None:
            skipped += 1
            continue
        first_start = min(first_start, symbol_start) if first_start else symbol_start
        try:
            frame = provider.fetch_daily_with_hfq(
                symbol,
                datetime.combine(symbol_start, datetime.min.time()),
                datetime.combine(target_end, datetime.min.time()),
            )
            if frame.empty:
                skipped += 1
            else:
                rows_saved += storage.save_bars(frame, timeframe="1d")
                updated += 1
            if refresh_dividends:
                dividends = provider.fetch_dividends(symbol)
                if not dividends.empty:
                    storage.save_cn_dividends(dividends)
        except Exception as exc:
            failed += 1
            logger.warning("stock %s failed: %s", symbol, exc)
        if index == 1 or index % 50 == 0 or index == len(symbols):
            logger.info("stocks %s/%s updated=%s skipped=%s failed=%s rows=%s", index, len(symbols), updated, skipped, failed, rows_saved)
    return StepSummary("stocks", len(symbols), updated, skipped, failed, rows_saved, first_start, target_end)


def _refresh_stock_dividends(
    provider: TushareProvider,
    storage: DuckDBStorage,
    ranges: Dict[str, SymbolRange],
    target_end: date,
    limit: int,
) -> StepSummary:
    symbols = _limit_items(sorted(ranges), limit)
    updated = skipped = failed = rows_saved = 0
    for index, symbol in enumerate(symbols, start=1):
        try:
            dividends = provider.fetch_dividends(symbol)
            if dividends.empty:
                skipped += 1
            else:
                rows_saved += storage.save_cn_dividends(dividends)
                updated += 1
        except Exception as exc:
            failed += 1
            logger.warning("dividends %s failed: %s", symbol, exc)
        if index == 1 or index % 50 == 0 or index == len(symbols):
            logger.info("dividends %s/%s updated=%s skipped=%s failed=%s rows=%s", index, len(symbols), updated, skipped, failed, rows_saved)
    return StepSummary("dividends", len(symbols), updated, skipped, failed, rows_saved, None, target_end)


def _update_index_bars(
    provider: TushareProvider,
    storage: DuckDBStorage,
    ranges: Dict[str, SymbolRange],
    indices: Sequence[str],
    target_end: date,
    explicit_start: Optional[date],
    full_start: date,
    overlap_days: int,
) -> StepSummary:
    symbols = sorted({*ranges.keys(), *indices})
    updated = skipped = failed = rows_saved = 0
    first_start: Optional[date] = None
    for symbol in symbols:
        symbol_range = ranges.get(symbol)
        current_end = symbol_range.end if symbol_range else None
        symbol_start = _next_start(current_end, target_end, explicit_start, full_start, overlap_days)
        if symbol_start is None:
            skipped += 1
            continue
        first_start = min(first_start, symbol_start) if first_start else symbol_start
        try:
            frame = provider.fetch_index_daily_with_hfq(
                symbol,
                datetime.combine(symbol_start, datetime.min.time()),
                datetime.combine(target_end, datetime.min.time()),
            )
            if frame.empty:
                skipped += 1
            else:
                rows_saved += storage.save_cn_index_bars(frame, timeframe="1d")
                updated += 1
        except Exception as exc:
            failed += 1
            logger.warning("index %s failed: %s", symbol, exc)
    return StepSummary("indices", len(symbols), updated, skipped, failed, rows_saved, first_start, target_end)


def _update_etf_bars_and_nav(
    provider: TushareProvider,
    storage: DuckDBStorage,
    etf_ranges: Dict[str, SymbolRange],
    nav_ranges: Dict[str, SymbolRange],
    target_end: date,
    explicit_start: Optional[date],
    full_start: date,
    overlap_days: int,
    limit: int,
    skip_nav: bool,
) -> StepSummary:
    symbols = _limit_items(sorted({*etf_ranges.keys(), *nav_ranges.keys()}), limit)
    updated = skipped = failed = rows_saved = 0
    first_start: Optional[date] = None
    for index, symbol in enumerate(symbols, start=1):
        bar_range = etf_ranges.get(symbol)
        bar_start = _next_start(
            bar_range.end if bar_range else None,
            target_end,
            explicit_start,
            full_start,
            overlap_days,
        )
        nav_range = nav_ranges.get(symbol)
        nav_start = None if skip_nav else _next_start(
            nav_range.end if nav_range else None,
            target_end,
            explicit_start,
            full_start,
            overlap_days,
        )
        if bar_start is None and nav_start is None:
            skipped += 1
            continue
        starts = [value for value in (bar_start, nav_start) if value is not None]
        symbol_start = min(starts)
        first_start = min(first_start, symbol_start) if first_start else symbol_start
        try:
            symbol_rows = 0
            if bar_start is not None:
                bars = provider.fetch_daily_with_hfq(
                    symbol,
                    datetime.combine(bar_start, datetime.min.time()),
                    datetime.combine(target_end, datetime.min.time()),
                )
                if not bars.empty:
                    symbol_rows += storage.save_bars(bars, timeframe="1d")
            if nav_start is not None:
                nav = provider.fetch_fund_nav(
                    symbol,
                    datetime.combine(nav_start, datetime.min.time()),
                    datetime.combine(target_end, datetime.min.time()),
                )
                if not nav.empty:
                    symbol_rows += storage.save_cn_fund_nav(nav)
            if symbol_rows:
                rows_saved += symbol_rows
                updated += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            logger.warning("etf/nav %s failed: %s", symbol, exc)
        if index == 1 or index % 50 == 0 or index == len(symbols):
            logger.info("etfs %s/%s updated=%s skipped=%s failed=%s rows=%s", index, len(symbols), updated, skipped, failed, rows_saved)
    return StepSummary("etfs_nav", len(symbols), updated, skipped, failed, rows_saved, first_start, target_end)


def _sidecar_start(path: Path, table: str, date_column: str, target_end: date) -> Optional[date]:
    max_date = _load_table_max_date(path, table, date_column)
    if max_date is None:
        return None
    candidate = max_date + timedelta(days=1)
    if candidate > target_end:
        return None
    return candidate


def _run_sidecar_step(
    name: str,
    start: Optional[date],
    target_end: date,
    callback: Callable[[date, date], object],
) -> StepSummary:
    if start is None:
        return StepSummary(name, 0, 0, 1, 0, 0, None, target_end)
    callback(start, target_end)
    return StepSummary(name, 1, 1, 0, 0, 0, start, target_end)


def _print_summaries(summaries: Iterable[StepSummary]) -> None:
    for item in summaries:
        logger.info(
            "%s requested=%s updated=%s skipped=%s failed=%s rows=%s start=%s end=%s",
            item.name,
            item.requested,
            item.updated,
            item.skipped,
            item.failed,
            item.rows,
            item.start or "",
            item.end,
        )


def _preflight_tushare(provider: TushareProvider, target_end: date) -> None:
    if provider._api is None:
        raise RuntimeError("Tushare API is not connected")
    start = target_end - timedelta(days=10)
    provider._rate_limit()
    try:
        frame = provider._api.trade_cal(
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=target_end.strftime("%Y%m%d"),
            fields="cal_date,is_open",
        )
    except Exception as exc:
        raise RuntimeError(f"Tushare preflight failed: {exc}") from exc
    if frame is None or frame.empty:
        raise RuntimeError("Tushare preflight returned no trade calendar rows")


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", default=_fmt_date(_default_target_date()), help="Target date YYYY-MM-DD; defaults to yesterday")
    parser.add_argument("--start", default=None, help="Optional explicit bar start date YYYY-MM-DD")
    parser.add_argument("--full-start", default="2011-01-01", help="Start date for empty tables")
    parser.add_argument("--duckdb-dir", default=str(DEFAULT_DUCKDB_DIR), help="Live DuckDB directory")
    parser.add_argument("--storage-backend", choices=("duckdb", "parquet-lake"), default="duckdb", help="Write daily market data to DuckDB sidecars or directly to the Parquet lake")
    parser.add_argument("--parquet-lake-root", default=str(DEFAULT_PARQUET_LAKE_ROOT), help="Local Parquet lake root used when --storage-backend=parquet-lake")
    parser.add_argument("--sync-parquet-lake", action="store_true", help="After a parquet-lake update, sync touched partitions to OSS with rclone")
    parser.add_argument("--parquet-lake-remote-prefix", default=DEFAULT_PARQUET_LAKE_REMOTE_PREFIX, help="rclone remote prefix for Parquet lake sync")
    parser.add_argument("--sync-dry-run", action="store_true", help="Print rclone sync commands without uploading")
    parser.add_argument("--indices", default=",".join(DEFAULT_MAJOR_INDICES), help="Comma-separated index symbols to update")
    parser.add_argument("--index-weight-codes", default=",".join(DEFAULT_INDEX_WEIGHT_CODES), help="Comma-separated Tushare index_weight codes")
    parser.add_argument("--min-interval", type=float, default=0.12)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--overlap-days", type=int, default=0)
    parser.add_argument("--stock-mode", choices=("date", "symbol"), default="date", help="Use date-based all-market stock updates for daily runs")
    parser.add_argument("--etf-mode", choices=("date", "symbol"), default="date", help="Use date-based ETF bar updates for daily runs")
    parser.add_argument("--limit-stocks", type=int, default=0)
    parser.add_argument("--limit-etfs", type=int, default=0)
    parser.add_argument("--skip-stocks", action="store_true")
    parser.add_argument("--skip-indices", action="store_true")
    parser.add_argument("--skip-etfs", action="store_true")
    parser.add_argument("--refresh-fund-nav", action="store_true", help="Refresh per-fund NAV rows; slow, not enabled for daily runs")
    parser.add_argument("--refresh-dividends", action="store_true", help="Refresh full per-symbol dividend history; slow, not enabled for daily runs")
    parser.add_argument("--dividends-only", action="store_true", help="Only refresh per-stock dividend history")
    parser.add_argument("--skip-daily-basic", action="store_true")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--skip-status", action="store_true")
    parser.add_argument("--skip-index-weights", action="store_true")
    parser.add_argument("--nav-only", action="store_true", help="Only refresh fund NAV rows in the ETF section")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    target_end = _parse_date(args.end)
    explicit_start = _parse_date(args.start) if args.start else None
    full_start = _parse_date(args.full_start)
    db_dir = Path(args.duckdb_dir)
    stock_db = db_dir / "cn_ohlcv.duckdb"
    etf_db = db_dir / "cn_etf_ohlcv.duckdb"
    index_db = db_dir / "cn_index_ohlcv.duckdb"
    daily_basic_db = db_dir / "cn_daily_basic.duckdb"
    financial_db = db_dir / "cn_financial_indicators.duckdb"
    status_db = db_dir / "cn_status.duckdb"
    index_weight_db = db_dir / "cn_index_weight.duckdb"
    fund_nav_db = db_dir / "cn_fund_nav.duckdb"
    corporate_actions_db = db_dir / "cn_corporate_actions.duckdb"
    summaries: List[StepSummary] = []
    if args.storage_backend == "parquet-lake":
        storage = ParquetLakeStorage(args.parquet_lake_root, auto_flush_manifest=False)
        stock_ranges = _lake_ranges(storage, "stock_ohlcv")
        index_ranges = _lake_ranges(storage, "index_ohlcv")
        etf_ranges = _lake_ranges(storage, "etf_ohlcv")
        nav_ranges = _lake_ranges(storage, "fund_nav")
        latest_etf_adj_factors = storage.load_latest_adj_factors("etf_ohlcv")
    else:
        storage = DuckDBStorage(
            db_path=str(stock_db),
            etf_db_path=str(etf_db),
            index_db_path=str(index_db),
            daily_basic_db_path=str(daily_basic_db),
            financial_indicator_db_path=str(financial_db),
            status_db_path=str(status_db),
            fund_nav_db_path=str(fund_nav_db),
            corporate_actions_db_path=str(corporate_actions_db),
        )
        stock_ranges = _load_ranges(stock_db, "daily_cn_ochl", "timestamp")
        index_ranges = _load_ranges(index_db, "daily_cn_ochl", "timestamp")
        etf_ranges = _load_ranges(etf_db, "daily_cn_ochl", "timestamp")
        nav_ranges = _load_ranges(fund_nav_db, "cn_fund_nav", "nav_date")
        latest_etf_adj_factors = _load_latest_adj_factors(etf_db, "daily_cn_ochl")

    provider = TushareProvider(storage=_NoopProviderStorage(), min_interval=args.min_interval)
    provider.connect()
    try:
        _preflight_tushare(provider, target_end)
        if args.dividends_only:
            summaries.append(
                _refresh_stock_dividends(
                    provider,
                    storage,
                    stock_ranges,
                    target_end,
                    args.limit_stocks,
                )
            )
        elif not args.skip_stocks:
            if args.stock_mode == "date" and not args.refresh_dividends:
                summaries.append(
                    _update_stock_bars_by_date(
                        provider,
                        storage,
                        stock_ranges,
                        target_end,
                        explicit_start,
                        full_start,
                        args.limit_stocks,
                    )
                )
            else:
                summaries.append(
                    _update_stock_bars(
                        provider,
                        storage,
                        stock_ranges,
                        target_end,
                        explicit_start,
                        full_start,
                        args.overlap_days,
                        args.limit_stocks,
                        refresh_dividends=args.refresh_dividends,
                    )
                )
        if not args.skip_indices:
            indices = [item.strip() for item in args.indices.split(",") if item.strip()]
            summaries.append(
                _update_index_bars(
                    provider,
                    storage,
                    index_ranges,
                    indices,
                    target_end,
                    explicit_start,
                    full_start,
                    args.overlap_days,
                )
            )
        if not args.skip_etfs:
            if args.nav_only:
                summaries.append(
                    _update_fund_nav_by_symbol(
                        provider,
                        storage,
                        {**etf_ranges, **nav_ranges},
                        target_end,
                        explicit_start,
                        full_start,
                        args.overlap_days,
                        args.limit_etfs,
                    )
                )
            elif args.etf_mode == "date" and not args.refresh_fund_nav:
                summaries.append(
                    _update_etf_bars_by_date(
                        provider,
                        storage,
                        etf_ranges,
                        latest_etf_adj_factors,
                        target_end,
                        explicit_start,
                        full_start,
                        args.limit_etfs,
                    )
                )
            else:
                summaries.append(
                    _update_etf_bars_and_nav(
                        provider,
                        storage,
                        etf_ranges,
                        nav_ranges,
                        target_end,
                        explicit_start,
                        full_start,
                        args.overlap_days,
                        args.limit_etfs,
                        skip_nav=not args.refresh_fund_nav,
                    )
                )
    finally:
        provider.disconnect()
        if args.storage_backend == "duckdb":
            storage.close()
        else:
            storage.flush_manifest()

    if not args.skip_daily_basic:
        start = explicit_start or (
            _lake_sidecar_start(storage, "daily_basic", target_end)
            if args.storage_backend == "parquet-lake"
            else _sidecar_start(daily_basic_db, "cn_daily_basic", "trade_date", target_end)
        )
        summaries.append(
            _run_sidecar_step(
                "daily_basic",
                start,
                target_end,
                lambda s, e: ingest_tushare_daily_basic(
                    market_db_path=stock_db,
                    basic_db_path=daily_basic_db,
                    start_date=_fmt_date(s),
                    end_date=_fmt_date(e),
                    min_interval=args.min_interval,
                    timeout=args.timeout,
                ),
            )
        )
        if args.storage_backend == "parquet-lake":
            rows = _copy_duckdb_range_to_lake(storage, daily_basic_db, "cn_daily_basic", "daily_basic", "trade_date", start, target_end)
            logger.info("daily_basic parquet lake bridge rows=%s", rows)
            storage.flush_manifest()
    if not args.skip_financials:
        start = explicit_start or (
            _lake_sidecar_start(storage, "financial_indicators", target_end)
            if args.storage_backend == "parquet-lake"
            else _sidecar_start(financial_db, "cn_financial_indicators", "ann_date", target_end)
        )
        summaries.append(
            _run_sidecar_step(
                "financial_indicators",
                start,
                target_end,
                lambda s, e: ingest_tushare_financial_indicators(
                    market_db_path=stock_db,
                    financial_db_path=financial_db,
                    start_date=_fmt_date(s),
                    end_date=_fmt_date(e),
                    fetch_mode="period",
                    min_interval=args.min_interval,
                    timeout=args.timeout,
                ),
            )
        )
        if args.storage_backend == "parquet-lake":
            rows = _copy_duckdb_range_to_lake(storage, financial_db, "cn_financial_indicators", "financial_indicators", "ann_date", start, target_end)
            logger.info("financial_indicators parquet lake bridge rows=%s", rows)
            storage.flush_manifest()
    if not args.skip_status:
        start = explicit_start or (
            _lake_sidecar_start(storage, "security_status", target_end)
            if args.storage_backend == "parquet-lake"
            else _sidecar_start(status_db, "cn_security_status_daily", "trade_date", target_end)
        )
        summaries.append(
            _run_sidecar_step(
                "security_status",
                start,
                target_end,
                lambda s, e: build_cn_security_status(
                    market_db=stock_db,
                    security_db=status_db,
                    start_date=_fmt_date(s),
                    end_date=_fmt_date(e),
                    min_interval=args.min_interval,
                    replace_all=False,
                ),
            )
        )
        if args.storage_backend == "parquet-lake":
            rows = _copy_duckdb_range_to_lake(storage, status_db, "cn_security_status_daily", "security_status", "trade_date", start, target_end)
            logger.info("security_status parquet lake bridge rows=%s", rows)
            storage.flush_manifest()
    if not args.skip_index_weights:
        codes = [item.strip() for item in args.index_weight_codes.split(",") if item.strip()]
        for code in codes:
            start = explicit_start or (
                _lake_sidecar_start(storage, "index_weight", target_end)
                if args.storage_backend == "parquet-lake"
                else _sidecar_start(index_weight_db, "cn_index_weight", "trade_date", target_end)
            )
            summaries.append(
                _run_sidecar_step(
                    f"index_weight:{code}",
                    start,
                    target_end,
                    lambda s, e, index_code=code: ingest_tushare_index_weight(
                        index_code=index_code,
                        weight_db_path=index_weight_db,
                        start_date=_fmt_date(s),
                        end_date=_fmt_date(e),
                        min_interval=args.min_interval,
                        timeout=args.timeout,
                    ),
                )
            )
            if args.storage_backend == "parquet-lake":
                rows = _copy_duckdb_range_to_lake(storage, index_weight_db, "cn_index_weight", "index_weight", "trade_date", start, target_end)
                logger.info("index_weight:%s parquet lake bridge rows=%s", code, rows)
                storage.flush_manifest()

    if args.storage_backend == "parquet-lake":
        if args.sync_parquet_lake:
            storage.sync_touched(args.parquet_lake_remote_prefix, dry_run=args.sync_dry_run)
        storage.close()
    _print_summaries(summaries)


if __name__ == "__main__":
    started = time.time()
    main()
    logger.info("update_cn_live_data finished in %.1fs", time.time() - started)
