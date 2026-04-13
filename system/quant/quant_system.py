#!/usr/bin/env python3
"""CLI entry point for Quant Trading System.

Usage:
    python quant_system.py --mode paper
    python quant_system.py --mode live --config config.yaml
    python quant_system.py --backtest --start 2025-01-01 --end 2025-12-31
"""

import argparse
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from quant.core.engine import Engine, SystemMode
from quant.core.events import EventBus, EventType
from quant.core.portfolio import Portfolio
from quant.core.risk import RiskEngine
from quant.core.scheduler import Scheduler
from quant.data.providers.yahoo import YahooProvider
from quant.data.providers.alpha_vantage import AlphaVantageProvider
from quant.data.providers.futu import FutuProvider
from quant.data.storage import Storage
from quant.execution.brokers.paper import PaperBroker
from quant.execution.order_manager import OrderManager
from quant.execution.fill_handler import FillHandler
from quant.strategies.examples.momentum_eod import MomentumEOD
from quant.strategies.examples.mean_reversion import MeanReversion1m
from quant.strategies.examples.dual_thrust import DualThrust
from quant.utils.config_loader import ConfigLoader
from quant.utils.logger import setup_logger


class QuantSystem:
    """Main system orchestrator."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_loader = ConfigLoader(config_path)
        self.config = self.config_loader.load("config.yaml")
        self.logger = setup_logger(
            "QuantSystem",
            self.config.get("system", {}).get("log_level", "INFO"),
        )
        self.engine: Optional[Engine] = None
        self.storage: Optional[Storage] = None
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown handlers."""
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def _shutdown_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        self.logger.info("Shutdown signal received")
        if self.engine:
            self.engine.stop()
        sys.exit(0)

    def initialize(self) -> None:
        """Initialize all system components."""
        self.logger.info("Initializing Quant Trading System...")

        data_dir = self.config.get("system", {}).get("data_dir", "./data")
        self.storage = Storage(data_dir)

        self.engine = Engine(self.config)

        providers = self.config.get("data", {}).get("providers", [])
        for provider_name in providers:
            self._setup_provider(provider_name)

        brokers = self.config.get("execution", {}).get("brokers", ["paper"])
        for broker_name in brokers:
            self._setup_broker(broker_name)

        self._setup_strategies()

        self.logger.info("System initialization complete")

    def _setup_provider(self, provider_name: str) -> None:
        """Setup a data provider."""
        if provider_name == "yahoo":
            provider = YahooProvider()
            provider.connect()
            self.engine.set_data_provider("yahoo", provider)
            self.logger.info("Yahoo Finance provider initialized")
        elif provider_name == "alpha_vantage":
            api_key = self.config_loader.get("brokers.yaml", "alpha_vantage", "api_key", default="")
            provider = AlphaVantageProvider(api_key)
            provider.connect()
            self.engine.set_data_provider("alpha_vantage", provider)
            self.logger.info("Alpha Vantage provider initialized")
        elif provider_name == "futu":
            host = self.config_loader.get("brokers.yaml", "futu", "host", default="127.0.0.1")
            port = self.config_loader.get("brokers.yaml", "futu", "port", default=11111)
            provider = FutuProvider(host, port)
            provider.connect()
            self.engine.set_data_provider("futu", provider)
            self.logger.info("Futu provider initialized")

    def _setup_broker(self, broker_name: str) -> None:
        """Setup a broker adapter."""
        if broker_name == "paper":
            initial_cash = self.config.get("system", {}).get("initial_cash", 100000)
            slippage_bps = self.config.get("execution", {}).get("slippage_bps", 5)
            broker = PaperBroker(initial_cash, slippage_bps)
            broker.connect()
            self.engine.set_broker(broker)
            self.logger.info("Paper broker initialized")

    def _setup_strategies(self) -> None:
        """Setup and register strategies."""
        strategies_config = self.config.get("strategies", [])

        for strategy_cfg in strategies_config:
            if not strategy_cfg.get("enabled", False):
                continue

            name = strategy_cfg.get("name")
            symbols = strategy_cfg.get("symbols", [])

            if name == "MomentumEOD":
                strategy = MomentumEOD(symbols)
                self.engine.add_strategy(strategy)
                self.logger.info(f"Strategy {name} enabled")
            elif name == "MeanReversion1m":
                strategy = MeanReversion1m(symbols)
                self.engine.add_strategy(strategy)
                self.logger.info(f"Strategy {name} enabled")
            elif name == "DualThrust":
                strategy = DualThrust(symbols)
                self.engine.add_strategy(strategy)
                self.logger.info(f"Strategy {name} enabled")

    def run(self, mode: Optional[str] = None) -> None:
        """Run the system in specified mode."""
        if mode:
            self.config["system"]["mode"] = mode

        system_mode = self.config.get("system", {}).get("mode", "paper")

        if system_mode == "backtest":
            self._run_backtest()
        else:
            self._run_live()

    def _run_live(self) -> None:
        """Run in live or paper mode."""
        self.logger.info("Starting live/paper trading...")

        symbols = []
        for strat in self.engine.strategies:
            symbols.extend(strat.symbols)

        if symbols:
            self.engine.subscribe(list(set(symbols)))

        self.engine.start()

        try:
            while True:
                status = self.engine.get_portfolio_status()
                self.logger.info(
                    f"Portfolio: NAV=${status['nav']:.2f}, "
                    f"Unrealized P&L=${status['total_unrealized_pnl']:.2f}, "
                    f"Realized P&L=${status['total_realized_pnl']:.2f}"
                )
                import time
                time.sleep(60)
        except KeyboardInterrupt:
            self.engine.stop()

    def _run_backtest(self) -> None:
        """Run backtest mode."""
        start_date_str = self.config.get("system", {}).get("start_date", "2025-01-01")
        end_date_str = self.config.get("system", {}).get("end_date", "2025-12-31")
        speed = self.config.get("system", {}).get("backtest_speed", "1x")

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

        self.logger.info(f"Starting backtest from {start_date} to {end_date}")

        self.engine.run_backtest(start_date, end_date, speed)

        status = self.engine.get_portfolio_status()
        self.logger.info("Backtest complete:")
        self.logger.info(f"  Final NAV: ${status['nav']:.2f}")
        self.logger.info(f"  Total Unrealized P&L: ${status['total_unrealized_pnl']:.2f}")
        self.logger.info(f"  Total Realized P&L: ${status['total_realized_pnl']:.2f}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Quant Trading System")
    parser.add_argument(
        "--mode",
        choices=["live", "paper", "backtest"],
        help="System operation mode",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to configuration file",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Run in backtest mode",
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Backtest start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="Backtest end date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--speed",
        type=str,
        choices=["1x", "10x", "100x", "end_of_day"],
        default="1x",
        help="Backtest speed",
    )

    args = parser.parse_args()

    mode = None
    if args.backtest:
        mode = "backtest"
    elif args.mode:
        mode = args.mode

    config_path = args.config

    system = QuantSystem(config_path)
    system.initialize()
    system.run(mode)


if __name__ == "__main__":
    main()
