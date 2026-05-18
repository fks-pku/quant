"""Backfill CN OHLC symbols present in daily_basic but absent from daily bars."""

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import duckdb
import pandas as pd

_PKG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_DIR.parent))

from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage


logger = logging.getLogger("backfill_missing_cn_ohlc")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKET_DB = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "quant.duckdb"
DEFAULT_BASIC_DB = ROOT / "quant" / "infrastructure" / "var" / "duckdb" / "cn_daily_basic.duckdb"
DAILY_CN_TABLE = "daily_cn_ochl"
BASIC_TABLE = "cn_daily_basic"
NON_SH_SZ_PREDICATE = "(symbol LIKE '920%' OR symbol LIKE '8%' OR symbol LIKE '4%')"


@dataclass(frozen=True)
class BackfillTask:
    symbol: str
    start: date
    end: date
    daily_basic_rows: int


@dataclass(frozen=True)
class BackfillSummary:
    requested_symbols: int
    succeeded_symbols: int
    failed_symbols: int
    saved_bar_rows: int
    saved_dividend_rows: int


@dataclass(frozen=True)
class FetchedBackfillTask:
    task: BackfillTask
    bars: pd.DataFrame
    dividends: pd.DataFrame
    error: Optional[str] = None


class _NoopStorage:
    def close(self) -> None:
        return None


class _BackfillTushareProvider(TushareProvider):
    _global_lock = threading.Lock()
    _global_last_request_at = 0.0

    def _rate_limit(self) -> None:
        with self._global_lock:
            elapsed = time.time() - self._global_last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            type(self)._global_last_request_at = time.time()


_THREAD_LOCAL = threading.local()


def missing_ohlc_tasks(
    market_db_path: Path = DEFAULT_MARKET_DB,
    basic_db_path: Path = DEFAULT_BASIC_DB,
    include_bse: bool = False,
    limit: Optional[int] = None,
) -> List[BackfillTask]:
    conn = duckdb.connect(":memory:")
    try:
        _attach_db(conn, "market", market_db_path)
        _attach_db(conn, "basic", basic_db_path)
        symbol_filter = "TRUE" if include_bse else f"NOT {NON_SH_SZ_PREDICATE}"
        query = f"""
            WITH basic_ranges AS (
                SELECT
                    symbol,
                    MIN(trade_date) AS first_date,
                    MAX(trade_date) AS last_date,
                    COUNT(*) AS daily_basic_rows
                FROM basic.{BASIC_TABLE}
                WHERE {symbol_filter}
                GROUP BY symbol
            ),
            ohlc_symbols AS (
                SELECT DISTINCT symbol
                FROM market.{DAILY_CN_TABLE}
            )
            SELECT b.symbol, b.first_date, b.last_date, b.daily_basic_rows
            FROM basic_ranges b
            LEFT JOIN ohlc_symbols o USING(symbol)
            WHERE o.symbol IS NULL
            ORDER BY b.symbol
        """
        if limit is not None:
            query += " LIMIT ?"
            rows = conn.execute(query, [limit]).fetchall()
        else:
            rows = conn.execute(query).fetchall()
    finally:
        conn.close()

    return [
        BackfillTask(symbol=row[0], start=row[1], end=row[2], daily_basic_rows=int(row[3]))
        for row in rows
    ]


def backfill_missing_cn_ohlc(
    market_db_path: Path = DEFAULT_MARKET_DB,
    basic_db_path: Path = DEFAULT_BASIC_DB,
    min_interval: float = 0.3,
    workers: int = 1,
    fetch_dividends: bool = True,
    retries: int = 1,
    retry_sleep: float = 5.0,
    include_bse: bool = False,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> BackfillSummary:
    tasks = missing_ohlc_tasks(
        market_db_path=market_db_path,
        basic_db_path=basic_db_path,
        include_bse=include_bse,
        limit=limit,
    )
    logger.info("Resolved %s missing symbols", len(tasks))
    if dry_run:
        for task in tasks[:20]:
            logger.info("  %s %s~%s daily_basic_rows=%s", task.symbol, task.start, task.end, task.daily_basic_rows)
        return BackfillSummary(len(tasks), 0, 0, 0, 0)

    storage = DuckDBStorage(str(market_db_path), read_only=False)
    succeeded = 0
    failed = 0
    saved_bar_rows = 0
    saved_dividend_rows = 0
    started_at = time.time()
    try:
        if workers <= 1:
            fetched_iter = (
                _fetch_task(
                    task,
                    min_interval=min_interval,
                    fetch_dividends=fetch_dividends,
                    retries=retries,
                    retry_sleep=retry_sleep,
                )
                for task in tasks
            )
            for idx, fetched in enumerate(fetched_iter, start=1):
                succeeded, failed, saved_bar_rows, saved_dividend_rows = _save_fetched_task(
                    storage,
                    fetched,
                    succeeded,
                    failed,
                    saved_bar_rows,
                    saved_dividend_rows,
                )
                _log_progress(idx, len(tasks), succeeded, failed, saved_bar_rows, started_at)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        _fetch_task,
                        task,
                        min_interval=min_interval,
                        fetch_dividends=fetch_dividends,
                        retries=retries,
                        retry_sleep=retry_sleep,
                    )
                    for task in tasks
                ]
                for idx, future in enumerate(as_completed(futures), start=1):
                    fetched = future.result()
                    succeeded, failed, saved_bar_rows, saved_dividend_rows = _save_fetched_task(
                        storage,
                        fetched,
                        succeeded,
                        failed,
                        saved_bar_rows,
                        saved_dividend_rows,
                    )
                    _log_progress(idx, len(tasks), succeeded, failed, saved_bar_rows, started_at)
    finally:
        storage.close()

    return BackfillSummary(len(tasks), succeeded, failed, saved_bar_rows, saved_dividend_rows)


def _fetch_task(
    task: BackfillTask,
    min_interval: float,
    fetch_dividends: bool,
    retries: int = 1,
    retry_sleep: float = 5.0,
) -> FetchedBackfillTask:
    provider = _thread_provider(min_interval)
    symbol_start = datetime.combine(task.start, datetime.min.time())
    symbol_end = datetime.combine(task.end, datetime.min.time())
    last_error: Optional[str] = None
    for attempt in range(max(retries, 1)):
        try:
            bars = provider.fetch_daily_with_hfq(task.symbol, symbol_start, symbol_end)
            if bars.empty:
                last_error = "empty bars"
            else:
                dividends = provider.fetch_dividends(task.symbol) if fetch_dividends else pd.DataFrame()
                return FetchedBackfillTask(task=task, bars=bars, dividends=dividends)
        except Exception as exc:
            last_error = str(exc)
        if attempt + 1 < max(retries, 1):
            sleep_s = retry_sleep * float(attempt + 1)
            logger.warning("  %s: retrying after %s (attempt %s/%s)", task.symbol, last_error, attempt + 1, retries)
            time.sleep(sleep_s)
    return FetchedBackfillTask(task=task, bars=pd.DataFrame(), dividends=pd.DataFrame(), error=last_error)


def _thread_provider(min_interval: float) -> _BackfillTushareProvider:
    provider = getattr(_THREAD_LOCAL, "provider", None)
    if provider is None:
        provider = _BackfillTushareProvider(storage=_NoopStorage(), min_interval=min_interval)
        provider.connect()
        _THREAD_LOCAL.provider = provider
    return provider


def _save_fetched_task(
    storage: DuckDBStorage,
    fetched: FetchedBackfillTask,
    succeeded: int,
    failed: int,
    saved_bar_rows: int,
    saved_dividend_rows: int,
) -> tuple[int, int, int, int]:
    task = fetched.task
    if fetched.error:
        failed += 1
        logger.warning("  %s: FAILED - %s", task.symbol, fetched.error)
        return succeeded, failed, saved_bar_rows, saved_dividend_rows
    if fetched.bars.empty:
        failed += 1
        logger.warning("  %s: no bars returned for %s~%s", task.symbol, task.start, task.end)
        return succeeded, failed, saved_bar_rows, saved_dividend_rows

    saved_bar_rows += storage.save_bars(fetched.bars, timeframe="1d")
    div_rows = 0
    if not fetched.dividends.empty:
        div_rows = storage.save_cn_dividends(fetched.dividends)
        saved_dividend_rows += div_rows
    succeeded += 1
    logger.info("  %s: %s bars, %s dividends", task.symbol, len(fetched.bars), div_rows)
    return succeeded, failed, saved_bar_rows, saved_dividend_rows


def _log_progress(
    idx: int,
    total: int,
    succeeded: int,
    failed: int,
    saved_bar_rows: int,
    started_at: float,
) -> None:
    if idx != 1 and idx % 10 != 0 and idx != total:
        return
    elapsed = max(time.time() - started_at, 0.001)
    rate = idx / elapsed
    remaining = (total - idx) / rate if rate > 0 else 0
    logger.info(
        "Progress: %s/%s (%s ok, %s failed, %s bars), ETA %.1f min",
        idx,
        total,
        succeeded,
        failed,
        saved_bar_rows,
        remaining / 60.0,
    )


def _attach_db(conn: duckdb.DuckDBPyConnection, alias: str, path: Path) -> None:
    escaped = str(path).replace("'", "''")
    conn.execute(f"ATTACH '{escaped}' AS {alias} (READ_ONLY)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill CN OHLC rows missing from daily_basic coverage")
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument("--basic-db", type=Path, default=DEFAULT_BASIC_DB)
    parser.add_argument("--min-interval", type=float, default=0.3)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--skip-dividends", action="store_true")
    parser.add_argument("--include-bse", action="store_true")
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-sleep", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    summary = backfill_missing_cn_ohlc(
        market_db_path=args.market_db,
        basic_db_path=args.basic_db,
        min_interval=args.min_interval,
        workers=max(args.workers, 1),
        fetch_dividends=not args.skip_dividends,
        retries=max(args.retries, 1),
        retry_sleep=max(args.retry_sleep, 0.0),
        include_bse=args.include_bse,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    logger.info("Summary: %s", summary)


if __name__ == "__main__":
    main()
