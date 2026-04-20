"""Unified data preparation pipeline: Futu -> DuckDB.

All backtest data (HK + US) flows through this single pipeline.
Fetches historical K-line from Futu OpenD, stores in DuckDB.

Usage:
    python scripts/prepare_data.py --market all
    python scripts/prepare_data.py --market hk --start 2015-01-01
    python scripts/prepare_data.py --market us --start 2020-01-01 --end 2025-01-01
    python scripts/prepare_data.py --symbols HK.00700,HK.02318 --force

Requires: Futu OpenD running at 127.0.0.1:11111
"""

import argparse
import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from quant.data.providers.futu import FutuProvider
from quant.data.storage_duckdb import DuckDBStorage
from quant.utils.logger import setup_logger

logger = setup_logger("prepare_data")

DEFAULT_SYMBOLS = {
    "hk": [
        "HK.00700", "HK.00005", "HK.00006", "HK.00011", "HK.00012",
        "HK.00016", "HK.00017", "HK.00019", "HK.02318", "HK.02319",
        "HK.02628", "HK.02888", "HK.02899", "HK.06688", "HK.06888",
        "HK.09688",
    ],
    "us": [
        "US.AAPL", "US.MSFT", "US.GOOGL", "US.AMZN", "US.TSLA",
        "US.META", "US.NVDA", "US.JPM", "US.V", "US.WMT",
        "US.SPY", "US.QQQ", "US.IWM", "US.DIA",
    ],
}

CHUNK_DAYS = 365
RATE_LIMIT_SEC = 0.3


def _fetch_symbol_chunks(quote_ctx, symbol, start, end, timeframe="1d"):
    """Fetch bars for one symbol in yearly chunks with pagination."""
    ktype = "K_DAY" if timeframe in ("1d", "day", "daily") else FutuProvider.SUBTYPE_MAP.get(timeframe, "5m")
    all_chunks = []
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end)
        try:
            page_req_key = None
            chunk_count = 0
            while True:
                ret, data, page_req_key = quote_ctx.request_history_kline(
                    code=symbol,
                    start=chunk_start.strftime("%Y-%m-%d"),
                    end=chunk_end.strftime("%Y-%m-%d"),
                    ktype=ktype,
                    autype="qfq",
                    max_count=1000,
                    page_req_key=page_req_key,
                )
                if ret == 0 and data is not None and not data.empty:
                    df = data.copy()
                    df["timestamp"] = pd.to_datetime(df["time_key"])
                    df["symbol"] = symbol
                    cols = [c for c in ["timestamp", "symbol", "open", "high", "low", "close", "volume"] if c in df.columns]
                    all_chunks.append(df[cols])
                    chunk_count += len(df)
                if not page_req_key:
                    break

            logger.info(f"  {symbol} {chunk_start.date()}~{chunk_end.date()}: {chunk_count} bars")
        except Exception as e:
            logger.error(f"  {symbol} {chunk_start.date()}~{chunk_end.date()}: {e}")

        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(RATE_LIMIT_SEC)

    if not all_chunks:
        return pd.DataFrame()

    df = pd.concat(all_chunks, ignore_index=True)
    df = df.drop_duplicates(subset=["timestamp"], keep="first")
    return df.sort_values("timestamp").reset_index(drop=True)


def prepare(symbols, start, end, timeframe, db_path, force=False):
    """Fetch data from Futu and persist to DuckDB."""
    provider = FutuProvider()
    provider.connect()
    storage = DuckDBStorage(db_path)

    total_saved = 0
    total_skipped = 0

    for i, symbol in enumerate(symbols, 1):
        tag = f"[{i}/{len(symbols)}] {symbol}"

        existing = storage.get_date_range(symbol, timeframe)
        if existing and not force:
            if existing["end"] >= end - timedelta(days=7):
                logger.info(f"{tag}: up-to-date ({existing['end'].date()}), skip")
                total_skipped += 1
                continue
            fetch_start = existing["end"] + timedelta(days=1)
            logger.info(f"{tag}: incremental from {fetch_start.date()}")
        else:
            fetch_start = start
            logger.info(f"{tag}: full fetch {start.date()} ~ {end.date()}")

        df = _fetch_symbol_chunks(provider._quote_api, symbol, fetch_start, end, timeframe)
        if df.empty:
            logger.warning(f"{tag}: no data fetched")
            continue

        saved = storage.save_bars(df, timeframe)
        total_saved += saved
        logger.info(f"{tag}: saved {saved} bars")

    provider.disconnect()

    logger.info("Fetching instrument metadata (lot sizes)...")
    provider2 = FutuProvider()
    provider2.connect()
    try:
        ret, info_data = provider2._quote_api.get_stock_basicinfo(
            market='HK', stock_type='STOCK', code_list=symbols
        )
        if ret == 0 and info_data is not None and not info_data.empty:
            for _, row in info_data.iterrows():
                sym = row.get('code', '')
                lot = int(row.get('lot_size', 100))
                name = row.get('name', '')
                market = 'HK' if sym.startswith('HK.') else 'US'
                storage.save_instrument_meta(sym, lot, market, name)
                logger.info(f"  {sym}: lot_size={lot}, name={name}")
        else:
            for sym in symbols:
                storage.save_instrument_meta(sym, 100, 'HK' if sym.startswith('HK.') else 'US', '')
    except Exception as e:
        logger.warning(f"Failed to fetch instrument meta: {e}")
    provider2.disconnect()

    storage.close()
    logger.info(f"Done. saved={total_saved}, skipped={total_skipped}")


def main():
    parser = argparse.ArgumentParser(description="Unified pipeline: Futu -> DuckDB")
    parser.add_argument("--market", choices=["hk", "us", "all"], default="all")
    parser.add_argument("--symbols", help="Comma-separated Futu symbols (overrides --market)")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--db", default="./var/duckdb/quant.duckdb")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if up-to-date")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    elif args.market == "all":
        symbols = DEFAULT_SYMBOLS["hk"] + DEFAULT_SYMBOLS["us"]
    else:
        symbols = DEFAULT_SYMBOLS[args.market]

    logger.info(f"Pipeline: {len(symbols)} symbols, {start.date()} ~ {end.date()}, tf={args.timeframe}")
    logger.info(f"DB: {args.db}")
    prepare(symbols, start, end, args.timeframe, args.db, args.force)


if __name__ == "__main__":
    main()
