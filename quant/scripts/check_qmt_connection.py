#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.infrastructure.execution.brokers.qmt import QMTBroker
from quant.shared.utils.config_loader import ConfigLoader


def _load_qmt_config(config_dir: str = None) -> dict:
    loader = ConfigLoader(config_dir)
    return loader.load("brokers.yaml").get("qmt", {})


def main() -> int:
    parser = argparse.ArgumentParser(description="Check MiniQMT account connectivity without placing orders.")
    parser.add_argument("--config-dir", default=None, help="Directory containing brokers.yaml")
    args = parser.parse_args()

    cfg = _load_qmt_config(args.config_dir)
    broker = QMTBroker(
        host=cfg.get("host", "127.0.0.1"),
        port=cfg.get("port", 58610),
        account=cfg.get("account", ""),
        account_type=cfg.get("account_type", "STOCK"),
        password=cfg.get("password", ""),
        trade_mode=cfg.get("trade_mode", "SIMULATE"),
        userdata_mini_path=cfg.get("userdata_mini_path", ""),
        xtquant_path=cfg.get("xtquant_path", ""),
        mini_qmt_path=cfg.get("mini_qmt_path", ""),
    )

    try:
        broker.connect()
        account = broker.get_account_info()
        positions = broker.get_positions()
        print(f"connected=true account={account.account_id} currency={account.currency}")
        print(f"cash={account.cash:.2f} equity={account.equity:.2f} positions={len(positions)}")
        for pos in positions[:10]:
            print(
                f"position symbol={pos.symbol} quantity={pos.quantity:.0f} "
                f"avg_cost={pos.avg_cost:.4f} market_value={pos.market_value:.2f}"
            )
        return 0
    finally:
        broker.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
