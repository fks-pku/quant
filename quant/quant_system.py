#!/usr/bin/env python3
"""CLI entry point for Quant Trading System.

Usage:
    python quant_system.py --mode paper
    python quant_system.py --mode live --config config.yaml
    python quant_system.py --backtest --start 2025-01-01 --end 2026-05-31
"""

import argparse
import hashlib
import inspect
import json
import signal
import sys
import time
from datetime import date, datetime, timedelta
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
from quant.infrastructure.execution.strategy_ledger import sync_broker_trade_history
from quant.infrastructure.execution.strategy_controls import get_strategy_control
from quant.features.portfolio.tracker import StrategyPositionTracker, get_tracker
from quant.features.strategies.registry import StrategyRegistry
from quant.runtime.daily_strategy_runner import DailyRunResult
from quant.runtime.strategy_cycle import feed_strategy_bars, start_strategy
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
        self.live_recorder = self._create_live_recorder()
        self._strategy_tracker: Optional[StrategyPositionTracker] = None
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
        self.engine.set_strategy_signal_gate(self._strategy_accepts_live_signals)

        providers = self.config.get("data", {}).get("providers", [])
        for provider_name in providers:
            self._setup_provider(provider_name)

        brokers = self.config.get("execution", {}).get("brokers", ["paper"])
        for broker_name in brokers:
            self._setup_broker(broker_name)

        self._setup_order_manager()
        self._recover_live_strategy_history()
        self._setup_strategies()

        self.logger.info("System initialization complete")

    def initialize_live_recovery_only(self) -> None:
        """Initialize only the live broker path needed for read-only fill recovery."""
        self.logger.info("Initializing live trade-history recovery...")
        data_dir = self.config.get("system", {}).get("data_dir", "./data")
        self.storage = SQLiteStorage(data_dir)
        self._event_bus = EventBus()
        self.engine = Engine(self.config, event_bus=self._event_bus)
        brokers = self.config.get("execution", {}).get("brokers", [])
        for broker_name in brokers:
            self._setup_broker(broker_name)
        self._recover_live_strategy_history()
        self.logger.info("Live trade-history recovery complete")

    def _setup_provider(self, provider_name: str) -> None:
        """Setup a data provider."""
        if provider_name == "yahoo":
            provider = YahooProvider()
            provider.connect()
            self._register_data_provider("yahoo", provider)
            self.logger.info("Yahoo Finance provider initialized")
        elif provider_name == "alpha_vantage":
            api_key = self.config_loader.get("brokers.yaml", "alpha_vantage", "api_key", default="")
            provider = AlphaVantageProvider(api_key)
            provider.connect()
            self._register_data_provider("alpha_vantage", provider)
            self.logger.info("Alpha Vantage provider initialized")
        elif provider_name == "futu":
            host = self.config_loader.get("brokers.yaml", "futu", "host", default="127.0.0.1")
            port = self.config_loader.get("brokers.yaml", "futu", "port", default=11111)
            provider = FutuProvider(host, port)
            provider.connect()
            self._register_data_provider("futu", provider)
            self.logger.info("Futu provider initialized")
        elif provider_name == "duckdb":
            from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider

            duckdb_config = self.config.get("data", {}).get("duckdb", {})
            kwargs = {
                key: duckdb_config[key]
                for key in (
                    "db_path",
                    "use_security_status",
                    "status_db_path",
                    "parquet_lake_root",
                    "prefer_parquet_lake",
                )
                if key in duckdb_config
            }
            provider = DuckDBProvider(**kwargs)
            provider.connect()
            self._register_data_provider("duckdb", provider)
            self.logger.info("DuckDB provider initialized")

    def _register_data_provider(self, provider_name: str, provider: Any) -> None:
        self.engine.set_data_provider(provider_name, provider)
        data_config = self.config.get("data", {}) if isinstance(self.config, dict) else {}
        default_provider = data_config.get("default_provider")
        if default_provider == provider_name or "default" not in self.engine.data_providers:
            self.engine.set_data_provider("default", provider)

    def _setup_broker(self, broker_name: str) -> None:
        """Setup a broker adapter."""
        self._assert_broker_adapter_allowed(broker_name)
        if broker_name == "paper":
            initial_cash = self.config.get("system", {}).get("initial_cash", 10000)
            execution_config = self.config.get("execution", {})
            backtest_config = self.config.get("backtest", {})
            slippage_bps = execution_config.get("slippage_bps", 5)
            commission_config = execution_config.get("commission", {})
            execution_cost_model = execution_config.get("cost_model") or backtest_config.get("execution_cost_model")
            risk_price_deviation_limit = backtest_config.get(
                "risk_price_deviation_limit",
                execution_config.get("risk_price_deviation_limit", 0.15),
            )
            market_impact_factor = backtest_config.get(
                "market_impact_factor",
                execution_config.get("market_impact_factor", 0.0),
            )
            broker = PaperBroker(
                initial_cash,
                slippage_bps,
                data_provider=self.engine.data_providers.get("default"),
                commission_config=commission_config,
                execution_cost_model=execution_cost_model,
                risk_price_deviation_limit=risk_price_deviation_limit,
                market_impact_factor=market_impact_factor,
            )
            broker.set_portfolio(self.engine.portfolio)
            broker.set_portfolio_resolver(self._portfolio_for_strategy)
            broker.connect()
            self.engine.set_broker(broker)
            self.logger.info("Paper broker initialized")
        elif broker_name == "futu":
            host = self.config_loader.get("brokers.yaml", "futu", "host", default="127.0.0.1")
            port = self.config_loader.get("brokers.yaml", "futu", "port", default=11111)
            acc_list = self.config_loader.get("brokers.yaml", "futu", "acc_list", default={})
            password = self.config_loader.get("brokers.yaml", "futu", "password", default="")
            trade_mode = self.config_loader.get("brokers.yaml", "futu", "trade_mode", default="SIMULATE")
            self._assert_broker_mode_allowed("futu", trade_mode)
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
            self._assert_broker_mode_allowed("qmt", trade_mode)
            userdata_mini_path = self.config_loader.get("brokers.yaml", "qmt", "userdata_mini_path", default="")
            xtquant_path = self.config_loader.get("brokers.yaml", "qmt", "xtquant_path", default="")
            mini_qmt_path = self.config_loader.get("brokers.yaml", "qmt", "mini_qmt_path", default="")
            commission_config = self.config.get("execution", {}).get("commission", {})
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
                commission_config=commission_config,
            )
            broker.connect()
            self.engine.set_broker(broker)
            self.logger.info(f"QMT broker initialized (mode: {trade_mode})")

    def _assert_broker_adapter_allowed(self, broker_name: str) -> None:
        system_mode = str(self.config.get("system", {}).get("mode", "paper")).lower()
        if system_mode != "live" and str(broker_name).lower() != "paper":
            raise RuntimeError(
                f"paper mode only supports PaperBroker; refused broker={broker_name}"
            )

    def _assert_broker_mode_allowed(self, broker_name: str, trade_mode: str) -> None:
        system_mode = str(self.config.get("system", {}).get("mode", "paper")).lower()
        if system_mode != "live" and str(trade_mode).upper() == "REAL":
            raise RuntimeError(
                f"{broker_name} trade_mode=REAL is only allowed when system.mode=live"
            )

    def _setup_order_manager(self) -> None:
        """Setup order manager and fill handler, wire to engine."""
        strategy_tracker = self._strategy_tracker_for_mode()
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
        system_mode = self._system_mode_text()
        self.engine.execution_manager = (
            self._create_live_execution_manager(order_manager)
            if system_mode in {"live", "paper"}
            else None
        )
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

    def _recover_live_strategy_history(self) -> None:
        if self._system_mode_text() != "live":
            return
        live_config = self.config.get("live_trading", {}) if isinstance(self.config, dict) else {}
        recovery_config = live_config.get("recovery", {}) if isinstance(live_config, dict) else {}
        if isinstance(recovery_config, dict) and recovery_config.get("enabled", True) is False:
            return
        broker = self.engine.broker if self.engine else None
        if broker is None:
            return
        overlap_days = int((recovery_config or {}).get("history_overlap_days", 7) or 7)
        end_day = date.today()
        start_day = end_day - timedelta(days=max(overlap_days, 1))
        try:
            result = sync_broker_trade_history(
                broker=broker,
                recorder=self.live_recorder,
                tracker=self._strategy_tracker_for_mode(),
                mode="live",
                start_date=start_day,
                end_date=end_day,
                logger=self.logger,
            )
        except Exception as exc:
            self.logger.warning(f"Live strategy recovery skipped: {exc}")
            return
        if result.get("broker_history_supported"):
            self.logger.info(
                "Live strategy recovery imported=%s skipped=%s unresolved=%s",
                result.get("imported_count", 0),
                result.get("skipped_count", 0),
                result.get("unresolved_count", 0),
            )

    def _create_live_recorder(self) -> LiveTradingRecorder:
        record_dir = self._record_dir_for_mode()
        if record_dir is None:
            return LiveTradingRecorder()
        return LiveTradingRecorder(record_dir)

    def _strategy_tracker_for_mode(self) -> StrategyPositionTracker:
        if getattr(self, "_strategy_tracker", None) is not None:
            return self._strategy_tracker
        from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
        db_path = Path(__file__).resolve().parent / "infrastructure" / "var" / "strategy_dashboard.duckdb"
        store = StrategyStateStore(db_path)
        self._strategy_tracker = StrategyPositionTracker(store=store, mode=self._system_mode_text())
        return self._strategy_tracker

    def _strategy_state_store(self) -> Any:
        tracker = self._strategy_tracker_for_mode()
        store = getattr(tracker, "_store", None)
        if store is not None:
            return store
        from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
        db_path = Path(__file__).resolve().parent / "infrastructure" / "var" / "strategy_dashboard.duckdb"
        return StrategyStateStore(db_path)

    def _record_dir_for_mode(self) -> Optional[Path]:
        live_config = self.config.get("live_trading", {}) if isinstance(self.config, dict) else {}
        configured = live_config.get("record_dir") if isinstance(live_config, dict) else None
        if configured:
            return self._resolve_repo_path(configured)
        if self._system_mode_text() == "paper":
            return self._paper_runtime_dir()
        return None

    def _paper_runtime_dir(self) -> Path:
        return Path(__file__).resolve().parent / "infrastructure" / "var" / "paper_trading"

    def _resolve_repo_path(self, value: Any) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parent.parent / path

    def _system_mode_text(self) -> str:
        return str(self.config.get("system", {}).get("mode", "paper")).lower()

    def _strategy_accepts_live_signals(self, strategy_name: str) -> bool:
        system_mode = self._system_mode_text()
        if system_mode not in {"live", "paper"}:
            return True
        live_config = self.config.get("live_trading", {}) if isinstance(self.config, dict) else {}
        control_path = live_config.get("strategy_control_file") if isinstance(live_config, dict) else None
        control = get_strategy_control(
            strategy_name,
            control_path,
            default_live_enabled=True,
            mode=system_mode,
        )
        if control.accepts_live_signals:
            return True
        self.logger.info(
            f"Strategy {strategy_name} mode={system_mode} state={control.live_state}; new signals disabled"
        )
        return False

    def _create_live_execution_manager(self, order_manager: OrderManager) -> LiveExecutionManager:
        live_config = self.config.get("live_trading", {}) if isinstance(self.config, dict) else {}
        execution_config = live_config.get("execution", {}) if isinstance(live_config, dict) else {}
        base_execution = self.config.get("execution", {}) if isinstance(self.config, dict) else {}
        max_cost_bps = execution_config.get("max_cost_bps", base_execution.get("max_cost_bps", 30))
        base_slippage_bps = base_execution.get("slippage_bps", 5)
        execution_cost_model = (
            base_execution.get("cost_model")
            or self.config.get("backtest", {}).get("execution_cost_model")
        )
        default_market = (
            live_config.get("market")
            or self.config.get("system", {}).get("market")
        )
        deadline = execution_config.get("deadline")
        return LiveExecutionManager(
            order_manager,
            default_max_cost_bps=max_cost_bps,
            base_slippage_bps=base_slippage_bps,
            execution_cost_model=execution_cost_model,
            default_market=default_market,
            default_deadline=deadline,
        )

    def _on_fill(self, fill: Any) -> None:
        """Handle fill events from FillHandler."""
        strategy_name = str(getattr(fill, "strategy_name", "") or "")
        if not strategy_name or strategy_name == "default":
            return
        for strategy in self.engine.strategies:
            if getattr(strategy, "name", None) != strategy_name:
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
        order_id = str(trade.get("order_id", ""))
        quantity = float(trade.get("quantity", 0.0) or 0.0)
        price = float(trade.get("price", 0.0) or 0.0)
        order_manager = getattr(self.engine, "order_manager", None) if self.engine else None
        if order_manager is not None and hasattr(order_manager, "update_order_from_fill"):
            order_manager.update_order_from_fill(order_id, quantity, price)
        if not hasattr(self, "_fill_handler"):
            return
        self._fill_handler.process_fill(
            order_id=order_id,
            symbol=str(trade.get("symbol", "")),
            side=str(trade.get("side", "")),
            quantity=quantity,
            price=price,
            commission=float(trade.get("commission", 0.0) or 0.0),
            timestamp=trade.get("timestamp"),
            strategy_name=trade.get("strategy_name"),
        )

    def _record_live_strategy_snapshots(self, timestamp: Optional[datetime] = None) -> None:
        try:
            ts = timestamp or datetime.now()
            sub_portfolios = getattr(self.engine, "_sub_portfolios", {}) if self.engine else {}
            if sub_portfolios:
                for strategy in getattr(self.engine, "strategies", []) or []:
                    strategy_name = getattr(strategy, "name", None)
                    sub = sub_portfolios.get(strategy_name) if strategy_name else None
                    if sub is None:
                        continue
                    market_value = sum(
                        float(getattr(pos, "market_value", 0.0) or 0.0)
                        for pos in getattr(sub, "positions", {}).values()
                    )
                    self.live_recorder.record_strategy_snapshot(
                        timestamp=ts,
                        strategy_name=strategy_name,
                        nav=float(getattr(sub, "nav", 0.0) or 0.0),
                        market_value=market_value,
                        cash=float(getattr(sub, "cash", 0.0) or 0.0),
                        realized_pnl=float(getattr(sub, "total_realized_pnl", 0.0) or 0.0),
                        unrealized_pnl=float(getattr(sub, "total_unrealized_pnl", 0.0) or 0.0),
                    )
                return
            tracker = self._strategy_tracker_for_mode()
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
                timestamp=ts,
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
            symbols = self._resolve_strategy_symbols(strategy_cfg)
            params = dict(strategy_params.get(name, {}).get("parameters", {}) or {})

            strategy = self._create_strategy(name, symbols, params)
            if strategy:
                allocation_pct = self._strategy_allocation_pct(strategy_cfg)
                self.engine.add_strategy(strategy, allocation_pct=allocation_pct)
                self._restore_strategy_runtime_positions(strategy, name)
                if not self._defer_setup_runtime_checkpoint_restore():
                    self._restore_strategy_runtime_checkpoint(strategy, name)
                self.logger.info(f"Strategy {name} enabled")

    def _defer_setup_runtime_checkpoint_restore(self) -> bool:
        system_config = self.config.get("system", {}) if isinstance(self.config, dict) else {}
        return bool(system_config.get("daily_signal_date"))

    def _restore_strategy_runtime_positions(self, strategy: Any, strategy_name: str) -> None:
        tracker = self._strategy_tracker_for_mode()
        getter = getattr(tracker, "get_positions_for_strategy", None)
        if not callable(getter):
            return
        positions = getter(strategy_name)
        if not positions:
            return
        if hasattr(strategy, "_positions"):
            strategy._positions.update({
                symbol: float(data.get("qty", 0.0) or 0.0)
                for symbol, data in positions.items()
                if float(data.get("qty", 0.0) or 0.0) > 0
            })
        portfolio = self._portfolio_for_strategy(strategy_name)
        if portfolio is None or not hasattr(portfolio, "update_position"):
            return
        existing_positions = getattr(portfolio, "positions", None)
        if isinstance(existing_positions, dict) and existing_positions:
            return
        total_cost = 0.0
        restore_trade_date = date.today()
        for symbol, data in positions.items():
            qty = float(data.get("qty", 0.0) or 0.0)
            avg_cost = float(data.get("avg_cost", 0.0) or 0.0)
            market_value = float(data.get("market_value", 0.0) or 0.0)
            if qty <= 0 or avg_cost <= 0:
                continue
            price = market_value / qty if market_value > 0 else avg_cost
            cost = avg_cost * qty
            portfolio.update_position(
                symbol=symbol,
                quantity=qty,
                price=price,
                cost=cost,
                trade_date=restore_trade_date,
                lot_price=avg_cost,
            )
            total_cost += cost
        initial_cash = float(
            getattr(portfolio, "initial_cash", getattr(portfolio, "allocated_capital", 0.0)) or 0.0
        )
        if initial_cash > 0 and hasattr(portfolio, "cash"):
            try:
                portfolio.cash = max(initial_cash - total_cost, 0.0)
            except Exception:
                pass

    def _restore_strategy_runtime_checkpoint(
        self,
        strategy: Any,
        strategy_name: str,
        before_date: Optional[date] = None,
    ) -> bool:
        restore = getattr(strategy, "restore_checkpoint_state", None)
        if not callable(restore):
            return False
        store = self._strategy_state_store()
        mode = self._system_mode_text()
        state_row = store.get_latest_runtime_state(
            strategy_name=strategy_name,
            mode=mode,
            before_date=before_date.isoformat() if before_date is not None else None,
        )
        if state_row is not None:
            restore(state_row.get("state") or {})
            self.logger.info(
                "Restored strategy runtime checkpoint strategy=%s mode=%s as_of=%s stage=%s hash=%s",
                strategy_name,
                mode,
                state_row.get("as_of_date", ""),
                state_row.get("stage", ""),
                state_row.get("state_hash", ""),
            )
            return True
        return self._bootstrap_daily_runtime_checkpoint_from_signals(
            strategy,
            strategy_name,
            before_date=before_date,
        )

    def _restore_strategy_runtime_checkpoints_for_daily(
        self,
        signal_day: date,
        *,
        include_signal_day: bool,
    ) -> None:
        before_date = signal_day + timedelta(days=1) if include_signal_day else signal_day
        for strategy in self.engine.strategies:
            strategy_name = self._strategy_runtime_name(strategy)
            self._restore_strategy_runtime_checkpoint(
                strategy,
                strategy_name,
                before_date=before_date,
            )

    def _bootstrap_daily_runtime_checkpoint_from_signals(
        self,
        strategy: Any,
        strategy_name: str,
        *,
        before_date: Optional[date],
    ) -> bool:
        if not hasattr(strategy, "_last_rebalance_date") or not hasattr(strategy, "_days_since_rebalance"):
            return False
        store = self._strategy_state_store()
        latest_signal_date = store.get_latest_signal_date(
            strategy_name=strategy_name,
            mode=self._system_mode_text(),
            before_date=before_date.isoformat() if before_date is not None else None,
        )
        if not latest_signal_date:
            return False
        signal_day = self._coerce_date(latest_signal_date)
        state = {
            "positions": getattr(strategy, "_positions", {}).copy()
            if isinstance(getattr(strategy, "_positions", None), dict)
            else {},
            "daily_bar_state": {
                "last_rebalance_date": signal_day.isoformat(),
                "days_since_rebalance": 0,
            },
        }
        strategy.restore_checkpoint_state(state)
        self.logger.info(
            "Bootstrapped strategy runtime checkpoint strategy=%s mode=%s from latest signal_date=%s",
            strategy_name,
            self._system_mode_text(),
            signal_day.isoformat(),
        )
        return True

    def _record_strategy_runtime_checkpoints(self, as_of_date: date, *, stage: str) -> None:
        if not self.engine:
            return
        for strategy in self.engine.strategies:
            strategy_name = self._strategy_runtime_name(strategy)
            self._record_strategy_runtime_checkpoint(
                strategy,
                strategy_name,
                as_of_date=as_of_date,
                stage=stage,
            )

    def _record_strategy_runtime_checkpoint(
        self,
        strategy: Any,
        strategy_name: str,
        *,
        as_of_date: date,
        stage: str,
    ) -> Optional[dict]:
        checkpoint = getattr(strategy, "checkpoint_state", None)
        if not callable(checkpoint):
            return None
        store = self._strategy_state_store()
        mode = self._system_mode_text()
        previous = store.get_latest_runtime_state(
            strategy_name=strategy_name,
            mode=mode,
        )
        row = store.upsert_runtime_state(
            strategy_name=strategy_name,
            mode=mode,
            as_of_date=as_of_date.isoformat(),
            stage=stage,
            strategy_class=f"{strategy.__class__.__module__}.{strategy.__class__.__name__}",
            state=checkpoint(),
            schema_version=1,
            previous_state_hash=str((previous or {}).get("state_hash") or ""),
            config_hash=self._runtime_config_hash(),
            run_id=str(self.config.get("system", {}).get("run_id", "")) if isinstance(self.config, dict) else "",
        )
        self.logger.info(
            "Recorded strategy runtime checkpoint strategy=%s mode=%s as_of=%s stage=%s hash=%s",
            strategy_name,
            mode,
            row.get("as_of_date", ""),
            stage,
            row.get("state_hash", ""),
        )
        return row

    def _strategy_runtime_name(self, strategy: Any) -> str:
        return str(getattr(strategy, "name", "") or strategy.__class__.__name__)

    def _runtime_config_hash(self) -> str:
        try:
            raw = json.dumps(self.config, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            raw = str(self.config)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
                kwargs = self._strategy_constructor_kwargs(name, symbols, params)
                return StrategyRegistry.create(name, **kwargs)
            except Exception as e:
                self.logger.error(f"Failed to create strategy {name}: {e}")
                return None

        self.logger.warning(f"Unknown strategy: {name}")
        return None

    def _strategy_constructor_kwargs(self, name: str, symbols: list, params: dict) -> dict:
        kwargs = dict(params or {})
        cls = StrategyRegistry.get(name)
        if cls is None:
            return kwargs
        try:
            signature = inspect.signature(cls)
        except (TypeError, ValueError):
            return kwargs
        accepted = {
            param_name
            for param_name, param in signature.parameters.items()
            if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
        }
        has_var_kwargs = any(param.kind == param.VAR_KEYWORD for param in signature.parameters.values())
        if not has_var_kwargs:
            ignored = sorted(key for key in kwargs if key not in accepted)
            if ignored:
                self.logger.warning("Ignoring unsupported parameters for %s: %s", name, ignored)
            kwargs = {key: value for key, value in kwargs.items() if key in accepted}
        if symbols and (has_var_kwargs or "symbols" in accepted):
            kwargs.setdefault("symbols", list(symbols))
        return kwargs

    def _resolve_strategy_symbols(self, strategy_cfg: dict) -> list:
        symbols = strategy_cfg.get("symbols", [])
        if symbols:
            if isinstance(symbols, str):
                return [symbols]
            return [str(symbol) for symbol in symbols]
        universe = strategy_cfg.get("universe") or strategy_cfg.get("symbol_universe")
        if not universe:
            return []
        return self._load_universe_symbols(str(universe), strategy_cfg)

    def _load_universe_symbols(self, universe: str, strategy_cfg: dict) -> list:
        provider = self._default_data_provider()
        if provider is None or not hasattr(provider, "list_available_symbols"):
            raise RuntimeError(f"Strategy universe {universe} requires a default data provider")
        universe_key = universe.lower()
        if universe_key in {"cn", "cn_stock", "ashare", "a_share"}:
            market = "cn"
        else:
            raise ValueError(f"Unsupported strategy universe: {universe}")
        timeframe = self.config.get("data", {}).get("default_timeframe", "1d")
        symbols = list(provider.list_available_symbols(timeframe, market))
        as_of = strategy_cfg.get("universe_as_of") or self.config.get("system", {}).get("daily_signal_date")
        if as_of and hasattr(provider, "get_bars_for_symbols") and symbols:
            day = self._coerce_date(as_of)
            frame = provider.get_bars_for_symbols(symbols, datetime.combine(day, datetime.min.time()), datetime.combine(day, datetime.min.time()), timeframe)
            symbols = self._symbols_from_frame(frame)
        return [str(symbol) for symbol in symbols]

    def _default_data_provider(self) -> Any:
        if not self.engine:
            return None
        provider = self.engine.data_providers.get("default")
        if provider is not None:
            return provider
        if self.engine.data_providers:
            return next(iter(self.engine.data_providers.values()))
        return None

    def _symbols_from_frame(self, frame: Any) -> list:
        if frame is None or getattr(frame, "empty", False):
            return []
        if hasattr(frame, "__getitem__"):
            try:
                return list(dict.fromkeys(str(symbol) for symbol in frame["symbol"].tolist()))
            except Exception:
                pass
        values = []
        for row in frame:
            symbol = row.get("symbol") if isinstance(row, dict) else getattr(row, "symbol", None)
            if symbol:
                values.append(str(symbol))
        return list(dict.fromkeys(values))

    def _coerce_date(self, value: Any) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        return datetime.fromisoformat(text[:10]).date()

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

    def run_daily_snapshot_once(
        self,
        signal_date: Any,
        execution_date: Optional[Any] = None,
        warmup_days: Optional[int] = None,
        provider_name: Optional[str] = None,
    ) -> dict:
        if not self.engine:
            raise RuntimeError("System must be initialized before running a daily snapshot")
        self._assert_current_broker_safe_for_paper()
        signal_day = self._coerce_date(signal_date)
        execution_day = self._coerce_date(execution_date) if execution_date is not None else signal_day + timedelta(days=1)
        provider = self._select_snapshot_provider(provider_name)
        symbols = self._strategy_symbols()
        if not symbols:
            raise RuntimeError("No strategy symbols configured for daily snapshot")
        warmup_count = int(
            warmup_days
            if warmup_days is not None
            else self.config.get("live_trading", {}).get("daily_snapshot_warmup_days", 0)
        )
        pending_only = bool(self.config.get("execution", {}).get("record_pending_only", False))
        system_mode = self._system_mode_text()
        due_pending_signals = []
        if not pending_only and system_mode == "paper":
            due_pending_signals = self._due_pending_signals_for_submit(
                mode="paper",
                signal_day=signal_day,
                execution_day=execution_day,
            )
        for strategy in self.engine.strategies:
            start_strategy(strategy)
        self._restore_strategy_runtime_checkpoints_for_daily(
            signal_day,
            include_signal_day=pending_only or bool(due_pending_signals),
        )
        if warmup_count > 0 and not due_pending_signals:
            self._warmup_daily_strategies(provider, symbols, signal_day, warmup_count)
        bars = self._load_snapshot_bars(provider, symbols, signal_day)
        if not bars:
            raise RuntimeError(f"No daily bars loaded for {signal_day}")
        order_manager = getattr(self.engine, "order_manager", None)
        if not pending_only:
            self._prepare_paper_execution_context(provider, symbols, execution_day)
        if due_pending_signals:
            submit_result = self._submit_due_pending_signals_for_mode(
                mode="paper",
                signal_day=signal_day,
                execution_day=execution_day,
                signals=due_pending_signals,
            )
            self.logger.info(
                "Paper pending submit result signal_date=%s execution_date=%s pending=%s submitted=%s rejected=%s skipped=%s",
                submit_result["signal_date"],
                submit_result["execution_date"],
                submit_result["pending_count"],
                submit_result["submitted_count"],
                submit_result["rejected_count"],
                submit_result["skipped_count"],
            )
            self._record_live_strategy_snapshots(
                timestamp=datetime.combine(execution_day, datetime.min.time()).replace(hour=15),
            )
            self._record_strategy_runtime_checkpoints(
                execution_day,
                stage="post_execution",
            )
            return self._pending_signal_daily_results(signal_day, bars, due_pending_signals)
        signal_timestamp_set = False
        signal_submit_date_set = False
        execution_timestamp_set = False
        if pending_only and hasattr(order_manager, "set_signal_timestamp"):
            signal_timestamp = datetime.combine(signal_day, datetime.min.time()).replace(hour=15)
            order_manager.set_signal_timestamp(signal_timestamp)
            signal_timestamp_set = True
        if pending_only and hasattr(order_manager, "set_signal_submit_date"):
            order_manager.set_signal_submit_date(execution_day.isoformat())
            signal_submit_date_set = True
        if not pending_only and hasattr(order_manager, "set_execution_timestamp"):
            market = str(self.config.get("system", {}).get("market", "CN") or "CN")
            market_config = self.config.get("markets", {}).get(market, {})
            open_hour = int(market_config.get("open_hour", 9) or 9)
            open_minute = int(market_config.get("open_minute", 30) or 30)
            execution_timestamp = datetime.combine(execution_day, datetime.min.time()).replace(
                hour=open_hour,
                minute=open_minute,
            )
            order_manager.set_execution_timestamp(execution_timestamp)
            execution_timestamp_set = True
        try:
            results = self.engine.inject_daily_snapshot(signal_day, bars, execution_day)
        finally:
            if signal_timestamp_set and hasattr(order_manager, "clear_signal_timestamp"):
                order_manager.clear_signal_timestamp()
            if signal_submit_date_set and hasattr(order_manager, "clear_signal_submit_date"):
                order_manager.clear_signal_submit_date()
            if execution_timestamp_set and hasattr(order_manager, "clear_execution_timestamp"):
                order_manager.clear_execution_timestamp()
        if pending_only:
            self._record_live_strategy_snapshots(
                timestamp=datetime.combine(signal_day, datetime.min.time()).replace(hour=15),
            )
            self._record_strategy_runtime_checkpoints(
                signal_day,
                stage="post_signal_close",
            )
        else:
            self._record_live_strategy_snapshots(
                timestamp=datetime.combine(execution_day, datetime.min.time()).replace(hour=15),
            )
            self._record_strategy_runtime_checkpoints(
                execution_day,
                stage="post_execution",
            )
        return results

    def submit_due_pending_signals_once(
        self,
        signal_date: Any,
        execution_date: Any,
    ) -> dict:
        if not self.engine:
            raise RuntimeError("System must be initialized before submitting pending signals")
        if self._system_mode_text() != "live":
            raise RuntimeError("submit_due_pending_signals_once is only allowed in live mode")
        signal_day = self._coerce_date(signal_date)
        execution_day = self._coerce_date(execution_date)
        if signal_day is None or execution_day is None:
            raise ValueError("signal_date and execution_date are required")
        return self._submit_due_pending_signals_for_mode(
            mode="live",
            signal_day=signal_day,
            execution_day=execution_day,
        )

    def _due_pending_signals_for_submit(
        self,
        *,
        mode: str,
        signal_day: date,
        execution_day: date,
    ) -> list:
        store = self._strategy_state_store()
        return store.get_pending_signals_for_submit(
            mode=mode,
            signal_date=signal_day.isoformat(),
            submit_date=execution_day.isoformat(),
        )

    def _submit_due_pending_signals_for_mode(
        self,
        *,
        mode: str,
        signal_day: date,
        execution_day: date,
        signals: Optional[list] = None,
    ) -> dict:
        order_manager = getattr(self.engine, "order_manager", None)
        if order_manager is None:
            raise RuntimeError("OrderManager is required to submit pending signals")
        execution_manager = getattr(self.engine, "execution_manager", None)
        if execution_manager is None:
            raise RuntimeError("LiveExecutionManager is required to submit pending signals")

        if signals is None:
            signals = self._due_pending_signals_for_submit(
                mode=mode,
                signal_day=signal_day,
                execution_day=execution_day,
            )
        market = str(self.config.get("system", {}).get("market", "CN") or "CN")
        market_config = self.config.get("markets", {}).get(market, {})
        execution_timestamp = datetime.combine(execution_day, datetime.min.time()).replace(
            hour=int(market_config.get("open_hour", 9) or 9),
            minute=int(market_config.get("open_minute", 30) or 30),
        )
        resolver = self.engine._make_execution_reference_resolver()
        submitted = 0
        rejected = 0
        skipped = 0
        order_manager.set_execution_timestamp(execution_timestamp)
        try:
            for signal in signals:
                strategy_name = str(signal.get("strategy_name") or "default")
                if not self._strategy_accepts_live_signals(strategy_name):
                    skipped += 1
                    self.logger.info(
                        "Skip pending signal because strategy is not running: %s %s %s %s",
                        strategy_name,
                        signal.get("symbol"),
                        signal.get("side"),
                        signal.get("quantity"),
                    )
                    continue
                symbol = str(signal.get("symbol") or "")
                side = str(signal.get("side") or "").upper()
                quantity = float(signal.get("quantity") or 0.0)
                reference_price = float(signal.get("reference_price") or 0.0)
                execution_reference = resolver.resolve(symbol, side)
                if not symbol or side not in {"BUY", "SELL"} or quantity <= 0 or reference_price <= 0:
                    skipped += 1
                    self.logger.warning("Skip malformed pending signal: %s", signal)
                    continue
                if execution_reference is None:
                    skipped += 1
                    self.logger.warning("Skip pending signal without execution reference: %s %s", strategy_name, symbol)
                    continue
                cost_bps = self._pending_signal_cost_bps(signal)
                limit_price = execution_manager._execution_limit_price(side, execution_reference.price, cost_bps)
                metadata = {
                    "signal_id": str(signal.get("signal_id") or ""),
                    "submit_date": execution_day.isoformat(),
                    "execution_date": execution_day.isoformat(),
                    "cost_bps": cost_bps,
                    "execution_cost_bps": cost_bps,
                    "execution_reference_price": float(execution_reference.price),
                }
                order_id = order_manager.submit_existing_signal(
                    symbol=symbol,
                    quantity=quantity,
                    side=side,
                    order_type=str(signal.get("order_type") or "LIMIT").upper(),
                    price=limit_price,
                    strategy_name=strategy_name,
                    risk_price=limit_price,
                    signal_metadata=metadata,
                    client_order_id=str(signal.get("order_id") or ""),
                )
                if order_id is None:
                    rejected += 1
                else:
                    submitted += 1
        finally:
            if hasattr(order_manager, "clear_execution_timestamp"):
                order_manager.clear_execution_timestamp()
        return {
            "signal_date": signal_day.isoformat(),
            "execution_date": execution_day.isoformat(),
            "pending_count": len(signals),
            "submitted_count": submitted,
            "rejected_count": rejected,
            "skipped_count": skipped,
        }

    def _pending_signal_daily_results(self, signal_day: date, bars: list, signals: list) -> dict:
        results = {}
        for signal in signals:
            strategy_name = str(signal.get("strategy_name") or "default")
            if strategy_name in results:
                continue
            results[strategy_name] = DailyRunResult(
                trading_date=signal_day,
                ran=True,
                bar_count=len(bars),
            )
        return results

    def _pending_signal_cost_bps(self, signal: dict) -> float:
        for key in ("cost_bps", "execution_cost_bps"):
            value = signal.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
        live_config = self.config.get("live_trading", {}) if isinstance(self.config, dict) else {}
        execution_config = live_config.get("execution", {}) if isinstance(live_config, dict) else {}
        base_execution = self.config.get("execution", {}) if isinstance(self.config, dict) else {}
        return float(execution_config.get("max_cost_bps", base_execution.get("max_cost_bps", 30)) or 30)

    def _assert_current_broker_safe_for_paper(self) -> None:
        mode = str(self.config.get("system", {}).get("mode", "paper")).lower()
        broker = self.engine.broker if self.engine else None
        broker_name = str(getattr(broker, "name", getattr(broker, "_name", "")) or "").lower()
        if mode != "live" and broker_name != "paper":
            raise RuntimeError(
                f"Daily paper snapshot only supports PaperBroker; refused broker={broker_name or type(broker).__name__}"
            )
        trade_mode = str(getattr(broker, "_trade_mode", "") or "").upper()
        if mode != "live" and trade_mode == "REAL":
            raise RuntimeError("Daily paper snapshot refuses to use a REAL broker")

    def _prepare_paper_execution_context(self, provider: Any, symbols: list, execution_day: date) -> None:
        mode = str(self.config.get("system", {}).get("mode", "paper")).lower()
        broker = self.engine.broker if self.engine else None
        if mode != "paper" or broker is None or not hasattr(broker, "set_execution_bars"):
            return
        execution_bars = self._load_snapshot_bars(provider, symbols, execution_day)
        if not execution_bars:
            raise RuntimeError(
                f"Paper daily snapshot requires execution-date bars for {execution_day}; "
                "run it after daily data is updated."
            )
        if hasattr(broker, "set_previous_execution_bars"):
            previous_bars = self._latest_bars_before(provider, symbols, execution_day)
            broker.set_previous_execution_bars(previous_bars)
        broker.set_execution_bars(execution_bars, trading_date=execution_day)

    def _latest_bars_before(self, provider: Any, symbols: list, execution_day: date) -> list:
        start = execution_day - timedelta(days=10)
        end = execution_day - timedelta(days=1)
        records = self._load_snapshot_bars(provider, symbols, start, end)
        latest: dict[str, tuple[date, Any]] = {}
        for bar in records:
            bar_day = self._bar_date(bar)
            symbol = self._bar_symbol(bar)
            if bar_day is None or symbol is None or bar_day >= execution_day:
                continue
            current = latest.get(symbol)
            if current is None or bar_day > current[0]:
                latest[symbol] = (bar_day, bar)
        return [item for _, item in latest.values()]

    def _select_snapshot_provider(self, provider_name: Optional[str]) -> Any:
        if not self.engine:
            return None
        provider = None
        if provider_name:
            provider = self.engine.data_providers.get(provider_name)
        if provider is None:
            provider = self._default_data_provider()
        if provider is None:
            raise RuntimeError("Daily snapshot requires a data provider")
        return provider

    def _strategy_symbols(self) -> list:
        symbols = []
        for strategy in self.engine.strategies:
            symbols.extend(getattr(strategy, "symbols", []) or [])
        return list(dict.fromkeys(str(symbol) for symbol in symbols if symbol))

    def _warmup_daily_strategies(self, provider: Any, symbols: list, signal_day: date, warmup_days: int) -> None:
        start = signal_day - timedelta(days=warmup_days)
        end = signal_day - timedelta(days=1)
        bars = self._load_snapshot_bars(provider, symbols, start, end)
        if not bars:
            return
        bars_by_date: dict[date, list] = {}
        for bar in bars:
            bar_day = self._bar_date(bar)
            if bar_day is None or bar_day >= signal_day:
                continue
            bars_by_date.setdefault(bar_day, []).append(bar)
        for bar_day in sorted(bars_by_date):
            for strategy in self.engine.strategies:
                feed_strategy_bars(strategy, bars_by_date[bar_day])

    def _load_snapshot_bars(self, provider: Any, symbols: list, start_day: date, end_day: Optional[date] = None) -> list:
        end_day = end_day or start_day
        timeframe = self.config.get("data", {}).get("default_timeframe", "1d")
        start_dt = datetime.combine(start_day, datetime.min.time())
        end_dt = datetime.combine(end_day, datetime.min.time())
        if hasattr(provider, "get_bars_for_symbols"):
            frame = provider.get_bars_for_symbols(symbols, start_dt, end_dt, timeframe)
        else:
            frames = []
            for symbol in symbols:
                frames.append(provider.get_bars(symbol, start_dt, end_dt, timeframe))
            frame = frames
        return self._frame_records(frame)

    def _frame_records(self, frame: Any) -> list:
        if frame is None:
            return []
        if isinstance(frame, list):
            records = []
            for item in frame:
                records.extend(self._frame_records(item))
            return records
        if getattr(frame, "empty", False):
            return []
        if hasattr(frame, "to_dict"):
            return frame.to_dict("records")
        return list(frame)

    def _bar_date(self, bar: Any) -> Optional[date]:
        value = bar.get("timestamp") if isinstance(bar, dict) else getattr(bar, "timestamp", None)
        if value is None:
            value = bar.get("date") if isinstance(bar, dict) else getattr(bar, "date", None)
        if value is None:
            return None
        return self._coerce_date(value)

    def _bar_symbol(self, bar: Any) -> Optional[str]:
        for key in ("symbol", "ts_code", "code", "ticker"):
            value = bar.get(key) if isinstance(bar, dict) else getattr(bar, key, None)
            if value:
                return str(value)
        return None

    def disconnect_adapters(self) -> None:
        if not self.engine:
            return
        seen = set()
        for provider in self.engine.data_providers.values():
            provider_id = id(provider)
            if provider_id in seen:
                continue
            seen.add(provider_id)
            if hasattr(provider, "disconnect"):
                provider.disconnect()
        if self.engine.broker and hasattr(self.engine.broker, "disconnect"):
            self.engine.broker.disconnect()

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


def _resolve_cli_mode(args: argparse.Namespace) -> Optional[str]:
    if args.backtest:
        return "backtest"
    if args.simulate_daily:
        mode = args.mode or "paper"
        if mode == "backtest":
            raise ValueError("--simulate-daily only supports paper or live mode")
        return mode
    if args.mode:
        return args.mode
    return None


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
    parser.add_argument(
        "--simulate-daily",
        action="store_true",
        help="Run one paper/live daily snapshot through the trading pipeline",
    )
    parser.add_argument(
        "--signal-date",
        type=str,
        help="Daily snapshot signal date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--execution-date",
        type=str,
        help="Daily snapshot execution date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=None,
        help="Historical daily bars to feed before the signal date without submitting orders",
    )
    parser.add_argument(
        "--snapshot-provider",
        type=str,
        default=None,
        help="Data provider name to load daily snapshot bars",
    )
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Record D-day strategy signals for next-session submission without broker submission",
    )
    parser.add_argument(
        "--submit-pending-only",
        action="store_true",
        help="Submit due DB pending strategy signals without running strategy signal generation",
    )
    parser.add_argument(
        "--recover-trades-only",
        action="store_true",
        help="Read live broker trade history into strategy ledgers without generating or submitting orders",
    )

    args = parser.parse_args()

    try:
        mode = _resolve_cli_mode(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    config_path = args.config

    system = QuantSystem(config_path)
    if args.recover_trades_only and args.pending_only:
        raise SystemExit("--recover-trades-only cannot be combined with --pending-only")
    if args.recover_trades_only and args.simulate_daily:
        raise SystemExit("--recover-trades-only cannot be combined with --simulate-daily")
    if args.recover_trades_only and mode != "live":
        raise SystemExit("--recover-trades-only requires --mode live")
    if args.submit_pending_only and args.simulate_daily:
        raise SystemExit("--submit-pending-only cannot be combined with --simulate-daily")
    if args.submit_pending_only and args.pending_only:
        raise SystemExit("--submit-pending-only cannot be combined with --pending-only")
    if args.submit_pending_only and mode != "live":
        raise SystemExit("--submit-pending-only requires --mode live")
    if args.submit_pending_only and (not args.signal_date or not args.execution_date):
        raise SystemExit("--submit-pending-only requires --signal-date and --execution-date")
    if args.pending_only and not args.simulate_daily:
        raise SystemExit("--pending-only requires --simulate-daily")
    if args.pending_only and mode not in {"live", "paper"}:
        raise SystemExit("--pending-only requires --mode live or --mode paper")
    if mode:
        system.config.setdefault("system", {})["mode"] = mode
    if args.recover_trades_only:
        try:
            system.initialize_live_recovery_only()
        finally:
            system.disconnect_adapters()
        return
    if args.pending_only:
        execution_config = system.config.setdefault("execution", {})
        execution_config["record_pending_only"] = True
        execution_config["brokers"] = ["paper"]
    if args.signal_date:
        system.config.setdefault("system", {})["daily_signal_date"] = args.signal_date
    system.initialize()
    if args.submit_pending_only:
        try:
            result = system.submit_due_pending_signals_once(args.signal_date, args.execution_date)
            system.logger.info(
                "Pending submit result signal_date=%s execution_date=%s pending=%s submitted=%s rejected=%s skipped=%s",
                result["signal_date"],
                result["execution_date"],
                result["pending_count"],
                result["submitted_count"],
                result["rejected_count"],
                result["skipped_count"],
            )
        finally:
            system.disconnect_adapters()
    elif args.simulate_daily:
        if not args.signal_date:
            raise SystemExit("--simulate-daily requires --signal-date")
        try:
            results = system.run_daily_snapshot_once(
                args.signal_date,
                execution_date=args.execution_date,
                warmup_days=args.warmup_days,
                provider_name=args.snapshot_provider,
            )
            for strategy_name, result in results.items():
                system.logger.info(
                    "Daily snapshot %s: ran=%s bars=%s missing=%s stale=%s",
                    strategy_name,
                    result.ran,
                    result.bar_count,
                    result.missing_symbols,
                    result.stale_symbols,
                )
        finally:
            system.disconnect_adapters()
    else:
        system.run(mode)


if __name__ == "__main__":
    main()
