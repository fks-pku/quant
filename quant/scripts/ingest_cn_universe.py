"""Batch ingest 15 years of CN market data from Tushare into DuckDB.

Downloads:
1. Major index daily bars (000001, 000300, 000905, 399001, 399006)
2. CSI 300 + CSI 500 constituent stocks (point-in-time)
3. For each stock:
   - Unadjusted daily bars (TRUE prices for execution)
   - HFQ adjustment factors → adj_open/adj_high/adj_low/adj_close (for signals)
   - Dividend history (cash + stock dividends for portfolio simulation)

Usage:
    # Full universe (CSI 300 + CSI 500), 15 years
    python quant/scripts/ingest_cn_universe.py --start 2011-01-01 --end 2025-12-31

    # Only index data (no stocks)
    python quant/scripts/ingest_cn_universe.py --start 2011-01-01 --end 2025-12-31 --index-only

    # Custom universe from a symbol list file
    python quant/scripts/ingest_cn_universe.py --start 2011-01-01 --end 2025-12-31 --symbol-file symbols.txt

    # Specific indices only
    python quant/scripts/ingest_cn_universe.py --start 2011-01-01 --end 2025-12-31 --indices 000300,000905

    # Test run: top 10 stocks only
    python quant/scripts/ingest_cn_universe.py --start 2023-01-01 --end 2024-01-01 --limit 10
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List

_pkg_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_pkg_dir.parent))

from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.shared.utils.logger import setup_logger

logger = setup_logger("ingest_cn_universe")

MAJOR_INDICES = ["000001", "000300", "000905", "399001", "399006", "399673", "000016"]


def ingest_indices(
    provider: TushareProvider,
    storage: DuckDBStorage,
    indices: List[str],
    start: datetime,
    end: datetime,
) -> None:
    logger.info(f"=== Ingesting {len(indices)} indices ===")
    for idx_code in indices:
        logger.info(f"Fetching index {idx_code} ({start.date()} ~ {end.date()})")
        try:
            df = provider.fetch_daily_with_hfq(idx_code, start, end)
            if df.empty:
                logger.warning(f"  No data for index {idx_code}")
                continue
            rows = storage.save_bars(df, timeframe="1d")
            logger.info(f"  Index {idx_code}: {rows} bars saved")
        except Exception as e:
            logger.error(f"  Failed index {idx_code}: {e}")
        time.sleep(0.35)


def ingest_stock(
    provider: TushareProvider,
    storage: DuckDBStorage,
    symbol: str,
    start: datetime,
    end: datetime,
) -> bool:
    try:
        df = provider.fetch_daily_with_hfq(symbol, start, end)
        if df.empty:
            logger.warning(f"  {symbol}: no daily bars")
            return False
        rows = storage.save_bars(df, timeframe="1d")

        div_df = provider.fetch_dividends(symbol)
        if not div_df.empty:
            storage.save_cn_dividends(div_df)

        div_count = len(div_df) if not div_df.empty else 0
        logger.info(f"  {symbol}: {rows} bars, {div_count} dividends")
        return True
    except Exception as e:
        logger.error(f"  {symbol}: FAILED - {e}")
        return False


def ingest_stocks(
    provider: TushareProvider,
    storage: DuckDBStorage,
    symbols: List[str],
    start: datetime,
    end: datetime,
    resume: bool = True,
) -> None:
    total = len(symbols)
    done = 0
    failed = 0
    skipped = 0

    if resume:
        existing = set(storage.get_symbols(timeframe="1d", market="cn"))
    else:
        existing = set()

    logger.info(f"=== Ingesting {total} stocks ({start.date()} ~ {end.date()}) ===")
    logger.info(f"  Resume mode: {'ON' if resume else 'OFF'}, {len(existing)} symbols already in DB")

    for i, symbol in enumerate(symbols):
        if resume and symbol in existing:
            date_range = storage.get_date_range(symbol, timeframe="1d")
            if date_range and date_range["end"] >= end:
                skipped += 1
                if (i + 1) % 50 == 0:
                    logger.info(f"  Progress: {i + 1}/{total} ({done} done, {skipped} skipped, {failed} failed)")
                continue

        ok = ingest_stock(provider, storage, symbol, start, end)
        if ok:
            done += 1
        else:
            failed += 1

        if (i + 1) % 20 == 0 or (i + 1) == total:
            logger.info(f"  Progress: {i + 1}/{total} ({done} done, {skipped} skipped, {failed} failed)")

        time.sleep(0.35)

    logger.info(f"=== Stock ingestion complete: {done} done, {skipped} skipped, {failed} failed ===")


def load_symbols_from_file(path: str) -> List[str]:
    symbols = []
    with open(path) as f:
        for line in f:
            sym = line.strip()
            if sym and sym.isdigit() and len(sym) == 6:
                symbols.append(sym)
    return symbols


def get_universe(
    provider: TushareProvider,
    indices_for_universe: List[str],
    limit: int = 0,
) -> List[str]:
    all_symbols = set()
    for idx_code in indices_for_universe:
        logger.info(f"Fetching constituents for index {idx_code}...")
        try:
            constituents = provider.fetch_index_constituents(idx_code)
            logger.info(f"  {idx_code}: {len(constituents)} constituents")
            all_symbols.update(constituents)
        except Exception as e:
            logger.error(f"  Failed to get constituents for {idx_code}: {e}")

    symbols = sorted(all_symbols)
    if limit > 0:
        symbols = symbols[:limit]
    logger.info(f"Total unique symbols in universe: {len(symbols)}")
    return symbols


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch ingest CN market data from Tushare")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--db-path", default=None, help="Path to DuckDB database")
    parser.add_argument("--index-only", action="store_true", help="Only download index data")
    parser.add_argument("--indices", default=None, help="Comma-separated index codes (default: major indices)")
    parser.add_argument("--universe-indices", default="000300,000905",
                        help="Comma-separated indices for stock universe (default: CSI300+CSI500)")
    parser.add_argument("--symbol-file", default=None, help="File with one symbol per line")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of stocks (0=no limit)")
    parser.add_argument("--no-resume", action="store_true", help="Don't skip already-ingested symbols")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    storage = DuckDBStorage(db_path=args.db_path) if args.db_path else DuckDBStorage()
    provider = TushareProvider(storage=storage)
    provider.connect()

    try:
        if args.indices:
            index_list = [x.strip() for x in args.indices.split(",")]
        else:
            index_list = MAJOR_INDICES

        ingest_indices(provider, storage, index_list, start_dt, end_dt)

        if not args.index_only:
            if args.symbol_file:
                symbols = load_symbols_from_file(args.symbol_file)
                logger.info(f"Loaded {len(symbols)} symbols from {args.symbol_file}")
            else:
                universe_indices = [x.strip() for x in args.universe_indices.split(",")]
                symbols = get_universe(provider, universe_indices, limit=args.limit)
            ingest_stocks(provider, storage, symbols, start_dt, end_dt, resume=not args.no_resume)

        logger.info("=== Ingestion complete ===")
        for table in storage.list_tables():
            if table.startswith("daily_") or table == "cn_dividends":
                count = storage.table_row_count(table)
                if count > 0:
                    logger.info(f"  {table}: {count} rows")

    finally:
        provider.disconnect()
        storage.close()


if __name__ == "__main__":
    main()
