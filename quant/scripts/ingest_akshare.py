"""CLI script to ingest China A-share daily data from akshare into DuckDB.

Usage:
    python quant/scripts/ingest_akshare.py --symbol 000001 --start 2019-01-01 --end 2025-01-01
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

_pkg_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_pkg_dir.parent))

import pandas as pd

from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.shared.utils.logger import setup_logger

logger = setup_logger("ingest_akshare")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest A-share daily bars from akshare into DuckDB")
    parser.add_argument("--symbol", required=True, help="A-share symbol, e.g. 000001, 600519")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", ""], help="Adjustment type (default: qfq)")
    args = parser.parse_args()

    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed. Run: pip install akshare")
        sys.exit(1)

    start = args.start.replace("-", "")
    end = args.end.replace("-", "")

    logger.info(f"Fetching {args.symbol} from {args.start} to {args.end} (adjust={args.adjust})")
    df = ak.stock_zh_a_hist(
        symbol=args.symbol,
        period="daily",
        start_date=start,
        end_date=end,
        adjust=args.adjust,
    )

    if df.empty:
        logger.warning("No data returned. Exiting.")
        sys.exit(1)

    logger.info(f"Fetched {len(df)} rows")

    df = df.rename(columns={
        "日期": "timestamp",
        "股票代码": "symbol",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
    })

    df["adj_open"] = df["open"]
    df["adj_high"] = df["high"]
    df["adj_low"] = df["low"]
    df["adj_close"] = df["close"]
    df["adj_factor"] = 1.0

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["symbol"] = df["symbol"].astype(str)

    storage = DuckDBStorage()
    rows = storage.save_bars(df, timeframe="1d")
    logger.info(f"Saved {rows} bars to DuckDB")
    storage.close()


if __name__ == "__main__":
    main()
