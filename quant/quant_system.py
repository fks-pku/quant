#!/usr/bin/env python3
"""CLI entry point for Quant Trading System.

Usage:
    python quant_system.py --mode paper
    python quant_system.py --mode live --config config.yaml
    python quant_system.py --backtest --start 2025-01-01 --end 2026-05-31
"""

import argparse
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant.features.trading.engine import Engine, SystemMode
from quant.infrastructure.events import EventBus, EventType
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.scheduler import Scheduler
from quant.infrastructure.data.providers.yahoo import YahooProvider
from quant.infrastructure.data.providers.alpha_vantage import AlphaVantageProvider
from quant.infrastructure.data.providers.futu import FutuProvider
from quant.infrastructure.data.storage import SQLiteStorage
from quant.infrastructure.execution.brokers.paper import PaperBroker
from quant.infrastructure.execution.brokers.futu import FutuBroker
from quant.infrastructure.execution.order_manager import OrderManager
from quant.infrastructure.execution.fill_handler import FillHandler
from quant.infrastructure.execution.live_executor import LiveExecutionManager
from quant.infrastructure.execution.live_recorder import LiveTradingRecorder
from quant.features.portfolio.tracker import get_tracker
from quant.features.strategies.registry import StrategyRegistry
from quant.shared.utils.config_loader import ConfigLoader
from quant.shared.utils.logger import setup_logger


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
        self.storage: Optional[SQLiteStorage] = None
        self.live_recorder = LiveTradingRecorder()
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
        self.storage = SQLiteStorage(data_dir)

        self._event_bus = EventBus()
        self.engine = Engine(self.config, event_bus=self._event_bus)

        providers = self.config.get("data", {}).get("providers", [])
        for provider_name in providers:
            self._setup_provider(provider_name)

        brokers = self.config.get("execution", {}).get("brokers", ["paper"])
        for broker_name in brokers:
            self._setup_broker(broker_name)

        self._setup_order_manager()
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
        elif broker_name == "futu":
            host = self.config_loader.get("brokers.yaml", "futu", "host", default="127.0.0.1")
            port = self.config_loader.get("brokers.yaml", "futu", "port", default=11111)
            acc_list = self.config_loader.get("brokers.yaml", "futu", "acc_list", default={})
            password = self.config_loader.get("brokers.yaml", "futu", "password", default="")
            trade_mode = self.config_loader.get("brokers.yaml", "futu", "trade_mode", default="SIMULATE")
            broker = FutuBroker(
                host=host,
                port=port,
                acc_list=acc_list,
                password=password,
                trade_mode=trade_mode,
            )
            broker.connect()
            if trade_mode == "REAL" and password:
                broker.unlock_trade(password=password, trade_mode=trade_mode)
            self.engine.set_broker(broker)
            self.logger.info(f"Futu broker initialized (mode: {trade_mode})")
        elif broker_name == "qmt":
            host = self.config_loader.get("brokers.yaml", "qmt", "host", default="127.0.0.1")
            port = self.config_loader.get("brokers.yaml", "qmt", "port", default=58610)
            account = self.config_loader.get("brokers.yaml", "qmt", "account", default="")
            account_type = self.config_loader.get("brokers.yaml", "qmt", "account_type", default="STOCK")
            password = self.config_loader.get("brokers.yaml", "qmt", "password", default="")
            trade_mode = self.config_loader.get("brokers.yaml", "qmt", "trade_mode", default="SIMULATE")
            userdata_mini_path = self.config_loader.get("brokers.yaml", "qmt", "userdata_mini_path", default="")
            xtquant_path = self.config_loader.get("brokers.yaml", "qmt", "xtquant_path", default="")
            mini_qmt_path = self.config_loader.get("brokers.yaml", "qmt", "mini_qmt_path", default="")
            from quant.infrastructure.execution.brokers.qmt import QMTBroker
            broker = QMTBroker(
                host=host,
                port=port,
                account=account,
                account_type=account_type,
                password=password,
                trade_mode=trade_mode,
                userdata_mini_path=userdata_mini_path,
                xtquant_path=xtquant_path,
                mini_qmt_path=mini_qmt_path,
            )
            broker.connect()
            self.engine.set_broker(broker)
            self.logger.info(f"QMT broker initialized (mode: {trade_mode})")

    def _setup_order_manager(self) -> None:
        """Setup order manager and fill handler, wire to engine."""
        strategy_tracker = get_tracker()
        order_manager = OrderManager(
            portfolio=self.engine.portfolio,
            risk_engine=self.engine.risk_engine,
            event_bus=self.engine.event_bus,
            config=self.config,
            strategy_tracker=strategy_tracker,
            live_recorder=self.live_recorder,
            risk_engine_resolver=self._risk_engine_for_strategy,
        )
        if self.engine.broker:
            broker_name = getattr(self.engine.broker, '_name', 'paper')
            order_manager.register_broker(broker_name, self.engine.broker)
        self.engine.set_order_manager(order_manager)
        self.engine.execution_manager = self._create_live_execution_manager(order_manager)
        self.logger.info("OrderManager initialized")

        fill_handler = FillHandler(
            portfolio=self.engine.portfolio,
            event_bus=self.engine.event_bus,
            config=self.config,
            strategy_tracker=strategy_tracker,
            live_recorder=self.live_recorder,
            portfolio_resolver=self._portfolio_for_strategy,
        )
        fill_handler.register_fill_callback(self._on_fill)
        self._fill_handler = fill_handler
        if self.engine.broker and hasattr(self.engine.broker, "register_trade_callback"):
            self.engine.broker.register_trade_callback(self._on_broker_trade)
        self.logger.info("FillHandler initialized")

    def _create_live_execution_manager(self, order_manager: OrderManager) -> LiveExecutionManager:
        live_config = self.config.get("live_trading", {}) if isinstance(self.config, dict) else {}
        execution_config = live_config.get("execution", {}) if isinstance(live_config, dict) else {}
        base_execution = self.config.get("execution", {}) if isinstance(self.config, dict) else {}
        max_cost_bps = execution_config.get("max_cost_bps", base_execution.get("max_cost_bps", 30))
        deadline = execution_config.get("deadline")
        return LiveExecutionManager(
            order_manager,
            default_max_cost_bps=max_cost_bps,
            default_deadline=deadline,
        )

    def _on_fill(self, fill: Any) -> None:
        """Handle fill events from FillHandler."""
        strategy_name = getattr(fill, "strategy_name", None)
        for strategy in self.engine.strategies:
            if strategy_name and getattr(strategy, "name", None) != strategy_name:
                continue
            if hasattr(strategy, "on_fill"):
                strategy.on_fill(strategy.context, fill)

    def _risk_engine_for_strategy(self, strategy_name: Optional[str]) -> Any:
        if not self.engine or not strategy_name:
            return self.engine.risk_engine if self.engine else None
        return self.engine._sub_risk_engines.get(strategy_name, self.engine.risk_engine)

    def _portfolio_for_strategy(self, strategy_name: Optional[str]) -> Any:
        if not self.engine or not strategy_name:
            return self.engine.portfolio if self.engine else None
        return self.engine._sub_portfolios.get(strategy_name, self.engine.portfolio)

    def _on_broker_trade(self, **trade: Any) -> None:
        if not hasattr(self, "_fill_handler"):
            return
        self._fill_handler.process_fill(
            order_id=str(trade.get("order_id", "")),
            symbol=str(trade.get("symbol", "")),
            side=str(trade.get("side", "")),
            quantity=float(trade.get("quantity", 0.0) or 0.0),
            price=float(trade.get("price", 0.0) or 0.0),
            commission=float(trade.get("commission", 0.0) or 0.0),
            timestamp=trade.get("timestamp"),
            strategy_name=trade.get("strategy_name"),
        )

    def _record_live_strategy_snapshots(self) -> None:
        try:
            tracker = get_tracker()
            broker_positions = self._broker_positions_for_tracker()
            if broker_positions:
                breakdown = tracker.calibrate(broker_positions)
            else:
                breakdown = tracker.get_breakdown()
            if not breakdown:
                return
            self.live_recorder.record_strategy_breakdown(
                breakdown,
                total_nav=self._broker_account_nav(),
                timestamp=datetime.now(),
            )
        except Exception as e:
            self.logger.error(f"Failed to record live strategy snapshots: {e}")

    def _broker_positions_for_tracker(self) -> list:
        if not self.engine or not self.engine.broker or not hasattr(self.engine.broker, "get_positions"):
            return []
        positions = []
        for pos in self.engine.broker.get_positions():
            positions.append({
                "symbol": getattr(pos, "symbol", ""),
                "quantity": getattr(pos, "quantity", 0.0),
                "avg_cost": getattr(pos, "avg_cost", 0.0),
                "market_value": getattr(pos, "market_value", 0.0),
                "unrealized_pnl": getattr(pos, "unrealized_pnl", 0.0),
            })
        return positions

    def _broker_account_nav(self) -> float:
        if self.engine and self.engine.broker and hasattr(self.engine.broker, "get_account_info"):
            account = self.engine.broker.get_account_info()
            return float(getattr(account, "equity", 0.0) or 0.0)
        status = self.engine.get_portfolio_status() if self.engine else {}
        return float(status.get("nav", 0.0) or 0.0)

    def _setup_strategies(self) -> None:
        """Setup and register strategies from both config.yaml and strategies.yaml."""
        strategies_config = self.config.get("strategies", [])

        strategy_params = {}
        try:
            strategy_params = self.config_loader.load("strategies.yaml").get("strategies", {})
        except Exception:
            pass

        for strategy_cfg in strategies_config:
            if not strategy_cfg.get("enabled", False):
                continue

            name = strategy_cfg.get("name")
            symbols = strategy_cfg.get("symbols", [])
            params = strategy_params.get(name, {}).get("parameters", {})

            if params:
                params["symbols"] = symbols

            strategy = self._create_strategy(name, symbols, params)
            if strategy:
                allocation_pct = self._strategy_allocation_pct(strategy_cfg)
                self.engine.add_strategy(strategy, allocation_pct=allocation_pct)
                self.logger.info(f"Strategy {name} enabled")

    def _strategy_allocation_pct(self, strategy_cfg: dict) -> Optional[float]:
        if "allocation_pct" in strategy_cfg:
            return float(strategy_cfg["allocation_pct"])

        live_config = self.config.get("live_trading", {}) if isinstance(self.config, dict) else {}
        allocation_cash = strategy_cfg.get(
            "allocation_cash",
            strategy_cfg.get("initial_cash", live_config.get("strategy_initial_cash")),
        )
        if allocation_cash is None:
            return None

        total_cash = float(self.config.get("system", {}).get("initial_cash", 0.0) or 0.0)
        if total_cash <= 0 and self.engine and getattr(self.engine, "portfolio", None):
            total_cash = float(getattr(self.engine.portfolio, "initial_cash", 0.0) or 0.0)
        if total_cash <= 0:
            raise ValueError("Strategy allocation requires positive system.initial_cash")
        return float(allocation_cash) / total_cash

    def _create_strategy(self, name: str, symbols: list, params: dict) -> Any:
        """Create a strategy instance by name using the registry."""
        if StrategyRegistry.is_registered(name):
            try:
                if params:
                    params["symbols"] = symbols
                    return StrategyRegistry.create(name, **params)
                else:
                    return StrategyRegistry.create(name, symbols=symbols)
            except Exception as e:
                self.logger.error(f"Failed to create strategy {name}: {e}")
                return None

        self.logger.warning(f"Unknown strategy: {name}")
        return None

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
                execution_manager = getattr(self.engine, "execution_manager", None)
                if execution_manager is not None:
                    execution_manager.drop_expired_targets()
                self._record_live_strategy_snapshots()
                time.sleep(60)
        except KeyboardInterrupt:
            self.engine.stop()

    def _run_backtest(self) -> None:
        """Run backtest mode."""
        start_date_str = self.config.get("system", {}).get("start_date", "2025-01-01")
        end_date_str = self.config.get("system", {}).get("end_date", "2026-05-31")
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
