#!/usr/bin/env python3
"""Migrate strategy dashboard JSONL inputs into strict state tables."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.scripts.strategy_dashboard_server import build_dashboard_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate strategy dashboard state into strategy_state.duckdb")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()

    payload = build_dashboard_payload(args.root)
    summary = {
        "ok": True,
        "strategy_state": payload.get("record_dirs", {}).get("strategy_state"),
        "strategy_count": len(payload.get("strategies", [])),
        "latest_market_data_date": payload.get("latest_market_data_date"),
        "latest_record_date": payload.get("latest_record_date"),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
