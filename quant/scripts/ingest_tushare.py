"""CLI script to ingest China A-share daily data from Tushare into DuckDB.

Usage:
    python quant/scripts/ingest_tushare.py --symbol 600519 --start 2023-01-01 --end 2024-01-01
    python quant/scripts/ingest_tushare.py --symbol 000300 --start 2023-01-01 --end 2024-01-01
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

_pkg_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_pkg_dir.parent))

from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.shared.utils.logger import setup_logger

logger = setup_logger("ingest_tushare")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest A-share daily bars from Tushare into DuckDB")
    parser.add_argument("--symbol", required=True, help="A-share symbol, e.g. 600519, 000300")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--db-path", default=None, help="Path to DuckDB database")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    storage = DuckDBStorage(args.db_path) if args.db_path else DuckDBStorage()
    provider = TushareProvider(storage=storage)
    provider.connect()

    logger.info(f"Fetching {args.symbol} from {args.start} to {args.end}")
    df = provider.get_bars(args.symbol, start_dt, end_dt, timeframe="1d")

    if df.empty:
        logger.warning("No data returned. Exiting.")
        sys.exit(1)

    logger.info(f"Fetched {len(df)} rows")
    logger.info("Data cached in DuckDB")
    provider.disconnect()


if __name__ == "__main__":
    main()
