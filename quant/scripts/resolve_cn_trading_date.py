#!/usr/bin/env python3
"""Resolve CN trading-calendar dates for scheduler scripts."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.infrastructure.execution.cn_trading_calendar import (
    DEFAULT_CACHE_PATH,
    DEFAULT_DUCKDB_DIR,
    is_open_trading_day,
    latest_data_date,
    latest_two_data_dates,
    next_trading_date_after,
    parse_date,
    previous_trading_date_before,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdb-dir", default=str(DEFAULT_DUCKDB_DIR))
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--no-refresh", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser("is-open")
    open_parser.add_argument("--date", required=True)

    next_parser = subparsers.add_parser("next")
    next_parser.add_argument("--date", required=True)

    previous_parser = subparsers.add_parser("previous")
    previous_parser.add_argument("--date", required=True)

    latest_parser = subparsers.add_parser("latest-data")
    latest_parser.add_argument("--on-or-before")

    latest_two_parser = subparsers.add_parser("latest-two-data")
    latest_two_parser.add_argument("--on-or-before")

    args = parser.parse_args(argv)
    duckdb_dir = Path(args.duckdb_dir)
    cache_path = Path(args.cache_path)
    allow_refresh = not bool(args.no_refresh)

    if args.command == "is-open":
        value = is_open_trading_day(
            parse_date(args.date),
            cache_path=cache_path,
            duckdb_dir=duckdb_dir,
            allow_refresh=allow_refresh,
        )
        print("1" if value else "0")
        return 0
    if args.command == "next":
        print(next_trading_date_after(
            parse_date(args.date),
            cache_path=cache_path,
            duckdb_dir=duckdb_dir,
            allow_refresh=allow_refresh,
        ).isoformat())
        return 0
    if args.command == "previous":
        print(previous_trading_date_before(
            parse_date(args.date),
            cache_path=cache_path,
            duckdb_dir=duckdb_dir,
            allow_refresh=allow_refresh,
        ).isoformat())
        return 0
    if args.command == "latest-data":
        print(latest_data_date(
            duckdb_dir=duckdb_dir,
            on_or_before=parse_date(args.on_or_before) if args.on_or_before else None,
        ).isoformat())
        return 0
    if args.command == "latest-two-data":
        signal, execution = latest_two_data_dates(
            duckdb_dir=duckdb_dir,
            on_or_before=parse_date(args.on_or_before) if args.on_or_before else None,
        )
        print(f"{signal.isoformat()},{execution.isoformat()}")
        return 0
    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
