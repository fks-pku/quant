"""Main event loop and orchestration engine."""

from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import threading
import time

from quant.domain.context import StrategyContext, StrategyScopedOrderManager
from quant.domain.ports.event_publisher import EventPublisher
from quant.domain.events.base import EventType
from quant.features.trading.scheduler import Scheduler
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.runtime.daily_strategy_runner import extract_bar_date, extract_bar_symbol, run_daily_snapshots
from quant.runtime.execution_reference import ExecutionReferencePriceResolver
from quant.runtime.strategy_cycle import after_trading, before_trading, feed_strategy_bars, start_strategy, stop_strategy
from quant.shared.utils.logger import setup_logger


class SystemMode(Enum):
    """System operation modes."""
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


class Context(StrategyContext):
    """Trading-engine strategy context — extends domain StrategyContext.

    Maintained as a subclass for backward compatibility with code that imports
    Context from this module. New code should import StrategyContext from domain.
    """

    def __init__(
        self,
        portfolio: Any,
        risk_engine: Any,
        event_bus: Any,
        order_manager: Any = None,
        execution_manager: Any = None,
        data_provider: Any = None,
        broker: Any = None,
        execution_reference_resolver: Any = None,
        signal_gate: Any = None,
        strategy_name: Optional[str] = None,
    ):
        scoped_order_manager = (
            StrategyScopedOrderManager(order_manager, strategy_name)
            if order_manager is not None
            else None
        )
        super().__init__(
            portfolio=portfolio,
            risk_engine=risk_engine,
            event_bus=event_bus,
            order_manager=scoped_order_manager,
            execution_manager=None,
            data_provider=data_provider,
            broker=None,
            execution_reference_resolver=execution_reference_resolver,
            signal_gate=signal_gate,
            strategy_name=str(strategy_name) if strategy_name else None,
        )
        self._order_manager = order_manager
        self._execution_manager = execution_manager
        self._broker = broker

    def submit_order(self, symbol: str, quantity: float, side: str,
                     order_type: str = "MARKET", price: Optional[float] = None,
                     strategy_name: Optional[str] = None,
                     execution_timing: Optional[str] = None) -> Optional[str]:
        timing = str(execution_timing or "").upper()
        if timing and timing != "NEXT_OPEN":
            raise ValueError(
                f"execution_timing={execution_timing!r} is only supported by the backtest context"
            )
        strategy_name = self._resolve_strategy_name(strategy_name)
        gate = getattr(self, "signal_gate", None)
        if callable(gate) and not gate(strategy_name):
            return None
        order_type_text = (order_type or "MARKET").upper()
        if self._execution_manager is not None and order_type_text == "MARKET":
            reference_price = price if isinstance(price, (int, float)) and price > 0 else None
            signal_reference = getattr(self._execution_manager, "get_signal_reference_price", None)
            if reference_price is None and callable(signal_reference):
                reference_price = signal_reference(symbol)
            resolver = getattr(self, "execution_reference_resolver", None)
            if reference_price is None and resolver is not None:
                reference = resolver.resolve(symbol, side, strategy_price=price)
                if reference is None:
                    return None
                reference_price = reference.price
            if reference_price is not None:
                return self._execution_manager.submit_target_order(
                    symbol, quantity, side, reference_price, strategy_name,
                )
        if self.order_manager is None:
            return None
        return self.order_manager.submit_order(
            symbol, quantity, side, order_type, price, strategy_name,
        )

    def _resolve_strategy_name(self, strategy_name: Optional[str]) -> Optional[str]:
        if not self.strategy_name:
            return strategy_name
        if strategy_name and str(strategy_name) != self.strategy_name:
            raise ValueError(
                f"strategy context for {self.strategy_name} cannot submit for {strategy_name}"
            )
        return self.strategy_name


class Engine:
    """Main event loop and orchestration engine."""

    def __init__(self, config: Dict[str, Any], event_bus: EventPublisher):
        self.config = config
        self.mode = SystemMode(config.get("system", {}).get("mode", "paper"))
        self.logger = setup_logger("Engine", config.get("system", {}).get("log_level", "INFO"))
        self.event_bus = event_bus
        self.portfolio = Portfolio(
            initial_cash=config.get("system", {}).get("initial_cash", 10000),
            currency=config.get("system", {}).get("currency", "USD"),
        )
        self.risk_engine = RiskEngine(config, self.portfolio, self.event_bus)
        self.scheduler = Scheduler(config, self.event_bus)
        self.order_manager = None
        self.execution_manager = None
        self.strategy_signal_gate: Optional[Callable[[str], bool]] = None
        live_config = config.get("live_trading", {})
        self._daily_snapshot_mode = bool(live_config.get("daily_snapshot_mode", True))
        self._strict_daily_snapshot = bool(live_config.get("strict_daily_snapshot", True))
        self._feed_intraday_bars = bool(live_config.get("feed_intraday_bars", False))
        reference_config = live_config.get("execution_reference", {})
        self._allow_strategy_reference_fallback = bool(reference_config.get("allow_strategy_price_fallback", False))

        self.strategies: List[Any] = []
        self.data_providers: Dict[str, Any] = {}
        self.broker: Optional[Any] = None

        self._sub_portfolios: Dict[str, Any] = {}
        self._sub_risk_engines: Dict[str, RiskEngine] = {}

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._subscribed_symbols: List[str] = []
        self._daily_bar_buffer: Dict[date, Dict[str, Any]] = {}
        self._completed_daily_snapshots: set[date] = set()
        self._daily_snapshot_results: Dict[tuple[date, str], Any] = {}

    def set_data_provider(self, name: str, provider: Any) -> None:
        """Set a data provider."""
        self.data_providers[name] = provider

    def set_broker(self, broker: Any) -> None:
        """Set the broker adapter."""
        self.broker = broker

    def set_order_manager(self, order_manager: Any) -> None:
        """Set the order manager."""
        self.order_manager = order_manager

    def add_strategy(self, strategy: Any, allocation_pct: Optional[float] = None) -> None:
        """Add a strategy to the engine and wire it to the event bus.

        allocation_pct: fraction of initial_cash allocated to this strategy (0.0-1.0).
            If provided, strategy gets a SubPortfolio with its own isolated capital.
            If None, strategy shares the master Portfolio (legacy mode).
        """
        sname = getattr(strategy, 'name', strategy.__class__.__name__)

        if allocation_pct is not None:
            from quant.features.trading.sub_portfolio import SubPortfolio
            alloc_cash = self.portfolio.initial_cash * allocation_pct
            sub = SubPortfolio(strategy_name=sname, allocated_capital=alloc_cash, master=self.portfolio)
            sub_risk = RiskEngine(self.config, sub, self.event_bus)
            self._sub_portfolios[sname] = sub
            self._sub_risk_engines[sname] = sub_risk
            pf = sub
            re = sub_risk
        else:
            pf = self.portfolio
            re = self.risk_engine

        strategy.context = Context(
            portfolio=pf,
            risk_engine=re,
            event_bus=self.event_bus,
            order_manager=self.order_manager,
            execution_manager=self.execution_manager,
            data_provider=self.data_providers.get("default"),
            broker=self.broker,
            execution_reference_resolver=self._make_execution_reference_resolver(),
            signal_gate=self._context_accepts_strategy_signals,
            strategy_name=sname,
        )
        self.strategies.append(strategy)

        self.event_bus.subscribe(EventType.BAR, lambda event: self._dispatch_bar(strategy, event))
        self.event_bus.subscribe(EventType.MARKET_OPEN, lambda event: self._dispatch_market_open(strategy, event))
        self.event_bus.subscribe(EventType.MARKET_CLOSE, lambda event: self._dispatch_market_close(strategy, event))

    def set_strategy_signal_gate(self, gate: Optional[Callable[[str], bool]]) -> None:
        self.strategy_signal_gate = gate

    def _make_execution_reference_resolver(self) -> ExecutionReferencePriceResolver:
        return ExecutionReferencePriceResolver(
            mode=self.mode.value,
            broker=self.broker,
            data_provider=self.data_providers.get("default"),
            allow_strategy_price_fallback=self._allow_strategy_reference_fallback,
        )

    def _dispatch_bar(self, strategy: Any, event: Any) -> None:
        if not self._daily_snapshot_mode:
            feed_strategy_bars(strategy, [event.data])
            return

        data = event.data
        trading_date = extract_bar_date(data) or self._event_trading_date(event)
        symbol = extract_bar_symbol(data)
        bucket = self._daily_bar_buffer.setdefault(trading_date, {})
        if symbol is None:
            symbol = f"__bar_{len(bucket) + 1}"
        bucket[symbol] = data
        if self._feed_intraday_bars and self._accepts_strategy_signals(strategy):
            feed_strategy_bars(strategy, [data])

    def _dispatch_market_open(self, strategy: Any, event: Any) -> None:
        trading_date = self._event_trading_date(event)
        if self._daily_snapshot_mode:
            self._run_completed_daily_snapshots(trading_date)
        if self._accepts_strategy_signals(strategy):
            before_trading(strategy, trading_date)

    def _dispatch_market_close(self, strategy: Any, event: Any) -> None:
        trading_date = self._event_trading_date(event)
        if self._daily_snapshot_mode:
            if trading_date in self._daily_bar_buffer:
                self._completed_daily_snapshots.add(trading_date)
            return
        if self._accepts_strategy_signals(strategy):
            after_trading(strategy, trading_date)

    def _run_completed_daily_snapshot(self, strategy: Any, current_date: date) -> None:
        self._run_completed_daily_snapshots(current_date)

    def _run_completed_daily_snapshots(self, current_date: date) -> None:
        eligible_dates = [
            trading_date
            for trading_date in self._completed_daily_snapshots
            if trading_date < current_date
        ]
        if not eligible_dates:
            return
        trading_date = max(eligible_dates)
        for stale_date in eligible_dates:
            if stale_date != trading_date:
                self._completed_daily_snapshots.discard(stale_date)
                self._daily_bar_buffer.pop(stale_date, None)
        bars = list(self._daily_bar_buffer.get(trading_date, {}).values())
        runnable_strategies = [
            strategy for strategy in self.strategies
            if self._accepts_strategy_signals(strategy)
        ]
        if not runnable_strategies:
            self._mark_snapshot_portfolios_from_bars(bars)
            self._completed_daily_snapshots.discard(trading_date)
            self._daily_bar_buffer.pop(trading_date, None)
            return
        self._set_execution_signal_bars(bars, trading_date)
        results = run_daily_snapshots(
            runnable_strategies,
            trading_date,
            bars,
            strict=self._strict_daily_snapshot,
            after_feed=self._mark_snapshot_portfolios,
        )
        if results and not any(result.ran for _, result in results):
            self._mark_snapshot_portfolios_from_bars(bars)
        for strategy, result in results:
            self._daily_snapshot_results[(trading_date, self._strategy_name(strategy))] = result
            if not result.ran:
                self.logger.warning(
                    "Skipped daily snapshot for %s on %s: missing=%s stale=%s",
                    self._strategy_name(strategy),
                    trading_date,
                    result.missing_symbols,
                    result.stale_symbols,
                )
        self._completed_daily_snapshots.discard(trading_date)
        self._daily_bar_buffer.pop(trading_date, None)

    def inject_daily_snapshot(
        self,
        trading_date: Any,
        bars: List[Any],
        execution_date: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Inject one EOD bar batch through the live daily-snapshot event path."""
        trading_day = self._coerce_date(trading_date)
        if trading_day is None:
            raise ValueError(f"Cannot coerce trading_date to date: {trading_date!r}")
        execution_day = self._coerce_date(execution_date) if execution_date is not None else trading_day + timedelta(days=1)
        if execution_day is None:
            raise ValueError(f"Cannot coerce execution_date to date: {execution_date!r}")

        for key in list(self._daily_snapshot_results):
            if key[0] == trading_day:
                self._daily_snapshot_results.pop(key, None)
        for bar in bars:
            self.event_bus.publish_nowait(EventType.BAR, bar, "daily_snapshot")
        self.event_bus.publish_nowait(EventType.MARKET_CLOSE, {"timestamp": trading_day})
        self.event_bus.publish_nowait(EventType.MARKET_OPEN, {"timestamp": execution_day})
        return {
            strategy_name: result
            for (result_day, strategy_name), result in self._daily_snapshot_results.items()
            if result_day == trading_day
        }

    def _strategy_name(self, strategy: Any) -> str:
        return getattr(strategy, "name", strategy.__class__.__name__)

    def _accepts_strategy_signals(self, strategy: Any) -> bool:
        return self._context_accepts_strategy_signals(self._strategy_name(strategy))

    def _context_accepts_strategy_signals(self, strategy_name: Optional[str]) -> bool:
        if self.strategy_signal_gate is None:
            return True
        if not strategy_name:
            return True
        try:
            return bool(self.strategy_signal_gate(str(strategy_name)))
        except Exception as exc:
            self.logger.error(f"Strategy signal gate failed for {strategy_name}: {exc}")
            return False

    def _mark_snapshot_portfolios(self, snapshot: Any) -> None:
        prices = self._snapshot_close_prices(snapshot.bars.values())
        for portfolio in self._snapshot_portfolios():
            self._update_portfolio_market_prices(portfolio, prices)

    def _mark_snapshot_portfolios_from_bars(self, bars: Any) -> None:
        prices = self._snapshot_close_prices(bars)
        for portfolio in self._snapshot_portfolios():
            self._update_portfolio_market_prices(portfolio, prices)

    def _set_execution_signal_bars(self, bars: Any, trading_date: date) -> None:
        manager = self.execution_manager
        setter = getattr(manager, "set_signal_bars", None)
        if callable(setter):
            setter(list(bars or []), trading_date=trading_date)

    def _snapshot_portfolios(self) -> List[Any]:
        portfolios: List[Any] = [self.portfolio]
        portfolios.extend(self._sub_portfolios.values())
        return portfolios

    @staticmethod
    def _snapshot_close_prices(bars: Any) -> Dict[str, float]:
        prices: Dict[str, float] = {}
        for bar in bars:
            symbol = extract_bar_symbol(bar)
            price = Engine._bar_close_price(bar)
            if symbol is not None and price is not None and price > 0:
                prices[symbol] = price
        return prices

    @staticmethod
    def _bar_close_price(bar: Any) -> Optional[float]:
        value = bar.get("close") if isinstance(bar, dict) else getattr(bar, "close", None)
        if value is None:
            getter = getattr(bar, "get", None)
            if callable(getter):
                value = getter("close", None)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _update_portfolio_market_prices(portfolio: Any, prices: Dict[str, float]) -> None:
        for symbol, position in getattr(portfolio, "positions", {}).items():
            quantity = float(getattr(position, "quantity", 0.0) or 0.0)
            price = prices.get(symbol)
            if quantity != 0 and price is not None and price > 0:
                position.update_market_price(price)

    def _event_trading_date(self, event: Any) -> date:
        data = getattr(event, "data", None)
        value = data.get("timestamp") if isinstance(data, dict) else getattr(data, "timestamp", None)
        if value is None:
            value = datetime.now()
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            text = value.strip()
            if len(text) == 8 and text.isdigit():
                return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            return datetime.fromisoformat(text[:10]).date()
        return datetime.now().date()

    def _coerce_date(self, value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if len(text) == 8 and text.isdigit():
                return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            return datetime.fromisoformat(text[:10]).date()
        return None

    def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to symbols for real-time data."""
        self._subscribed_symbols.extend(symbols)
        for name, provider in self.data_providers.items():
            if hasattr(provider, "subscribe"):
                provider.subscribe(symbols, lambda data, src=name: self._on_data(src, data))

    def _on_data(self, provider_name: str, data: Any) -> None:
        """Handle incoming data from providers — dispatches via EventBus."""
        if isinstance(data, dict):
            symbol = data.get("symbol")
            if symbol and self.broker and hasattr(self.broker, 'update_price'):
                self.broker.update_price(symbol, data.get("close", 0))
        self.event_bus.publish_nowait(EventType.BAR, data, provider_name)

    def start(self) -> None:
        """Start the engine in a background thread."""
        if self._running:
            return

        self.logger.info(f"Starting engine in {self.mode.value} mode")
        self._running = True

        for strategy in self.strategies:
            start_strategy(strategy)

        self.scheduler.start()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the engine."""
        if not self._running:
            return

        self.logger.info("Stopping engine")
        self._running = False

        self.scheduler.stop()

        for strategy in self.strategies:
            stop_strategy(strategy)

        for name, provider in self.data_providers.items():
            if hasattr(provider, "disconnect"):
                provider.disconnect()

        if self.broker and hasattr(self.broker, "disconnect"):
            self.broker.disconnect()

    def _run_loop(self) -> None:
        """Main engine loop for Mode A (Live) and Mode C (Paper)."""
        was_market_open = False
        while self._running:
            try:
                self._heartbeat()
                is_open = self._is_market_open()
                if is_open and not was_market_open:
                    self.event_bus.publish_nowait(
                        EventType.MARKET_OPEN,
                        {"timestamp": datetime.now()}
                    )
                elif not is_open and was_market_open:
                    self.event_bus.publish_nowait(
                        EventType.MARKET_CLOSE,
                        {"timestamp": datetime.now()}
                    )
                was_market_open = is_open
            except Exception as e:
                self.logger.error(f"Engine loop error: {e}")
            time.sleep(60)

    def _heartbeat(self) -> None:
        """Run periodic health checks."""
        if self.mode == SystemMode.LIVE:
            for name, provider in self.data_providers.items():
                if hasattr(provider, "is_connected") and not provider.is_connected():
                    self.logger.warning(f"Data provider {name} disconnected, attempting reconnect")
                    if hasattr(provider, "connect"):
                        provider.connect()

    def _is_market_open(self) -> bool:
        """Check if market is currently open."""
        from quant.shared.utils.datetime_utils import get_current_time, is_market_open

        system_config = self.config.get("system", {}) if isinstance(self.config, dict) else {}
        live_config = self.config.get("live_trading", {}) if isinstance(self.config, dict) else {}
        market = live_config.get("market", system_config.get("market", "US"))
        market_config = self.config.get("markets", {}).get(market, {})
        now = get_current_time(market_config.get("timezone", "America/New_York"))
        return is_market_open(
            now,
            market_config.get("open_hour", 9),
            market_config.get("open_minute", 30),
            market_config.get("close_hour", 16),
            market_config.get("close_minute", 0),
        )

    def run_backtest(self, start_date: datetime, end_date: datetime, speed: str = "1x") -> None:
        """Run backtest mode with historical data replay."""
        self.logger.info(f"Running backtest from {start_date} to {end_date} at {speed}")
        self.mode = SystemMode.BACKTEST

        speed_multiplier = {"1x": 1, "10x": 10, "100x": 100, "end_of_day": float("inf")}.get(speed, 1)

        for strategy in self.strategies:
            start_strategy(strategy)

        current_date = start_date
        while current_date <= end_date:
            self.event_bus.publish_nowait(
                EventType.MARKET_OPEN,
                {"timestamp": current_date},
            )
            for name, provider in self.data_providers.items():
                if hasattr(provider, "get_bars"):
                    data = provider.get_bars(
                        self._subscribed_symbols,
                        current_date,
                        current_date,
                        self.config.get("data", {}).get("default_timeframe", "5m"),
                    )
                    for _, row in data.iterrows():
                        self.event_bus.publish_nowait(EventType.BAR, row, name)

            self.event_bus.publish_nowait(
                EventType.MARKET_CLOSE,
                {"timestamp": current_date},
            )
            current_date = current_date + timedelta(days=1)

        if self._daily_snapshot_mode:
            flush_date = end_date + timedelta(days=1)
            flush_day = flush_date.date() if hasattr(flush_date, "date") else flush_date
            self._run_completed_daily_snapshots(flush_day)

        for strategy in self.strategies:
            stop_strategy(strategy)

    def get_portfolio_status(self) -> Dict[str, Any]:
        """Get current portfolio status."""
        status = self.portfolio.to_dict()
        if not self._sub_portfolios:
            return status
        strategy_portfolios = {
            name: sub.to_dict()
            for name, sub in self._sub_portfolios.items()
        }
        sub_nav = sum(float(item.get("nav", 0.0) or 0.0) for item in strategy_portfolios.values())
        sub_unrealized = sum(float(item.get("total_unrealized_pnl", 0.0) or 0.0) for item in strategy_portfolios.values())
        sub_realized = sum(float(item.get("total_realized_pnl", 0.0) or 0.0) for item in strategy_portfolios.values())
        status["nav"] = float(status.get("cash", 0.0) or 0.0) + sub_nav
        status["total_unrealized_pnl"] = float(status.get("total_unrealized_pnl", 0.0) or 0.0) + sub_unrealized
        status["total_realized_pnl"] = float(status.get("total_realized_pnl", 0.0) or 0.0) + sub_realized
        status["strategy_portfolios"] = strategy_portfolios
        return status
