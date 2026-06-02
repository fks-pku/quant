"""Main event loop and orchestration engine."""

from datetime import date, datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any
import threading
import time

from quant.domain.context import StrategyContext
from quant.domain.ports.event_publisher import EventPublisher
from quant.domain.events.base import EventType
from quant.features.trading.scheduler import Scheduler
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.runtime.daily_strategy_runner import extract_bar_date, extract_bar_symbol, run_daily_snapshot
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

    def submit_order(self, symbol: str, quantity: float, side: str,
                     order_type: str = "MARKET", price: Optional[float] = None,
                     strategy_name: Optional[str] = None) -> Optional[str]:
        order_type_text = (order_type or "MARKET").upper()
        if self.execution_manager is not None and order_type_text == "MARKET":
            reference_price = price
            resolver = getattr(self, "execution_reference_resolver", None)
            if resolver is not None:
                reference = resolver.resolve(symbol, side, strategy_price=price)
                if reference is None:
                    return None
                reference_price = reference.price
            if reference_price is not None:
                return self.execution_manager.submit_target_order(
                    symbol, quantity, side, reference_price, strategy_name,
                )
        if self.order_manager is None:
            return None
        return self.order_manager.submit_order(
            symbol, quantity, side, order_type, price, strategy_name,
        )


class Engine:
    """Main event loop and orchestration engine."""

    def __init__(self, config: Dict[str, Any], event_bus: EventPublisher):
        self.config = config
        self.mode = SystemMode(config.get("system", {}).get("mode", "paper"))
        self.logger = setup_logger("Engine", config.get("system", {}).get("log_level", "INFO"))
        self.event_bus = event_bus
        self.portfolio = Portfolio(
            initial_cash=config.get("system", {}).get("initial_cash", 100000),
            currency=config.get("system", {}).get("currency", "USD"),
        )
        self.risk_engine = RiskEngine(config, self.portfolio, self.event_bus)
        self.scheduler = Scheduler(config, self.event_bus)
        self.order_manager = None
        self.execution_manager = None
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
        self._daily_snapshot_processed: Dict[date, set[str]] = {}

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
        )
        self.strategies.append(strategy)

        self.event_bus.subscribe(EventType.BAR, lambda event: self._dispatch_bar(strategy, event))
        self.event_bus.subscribe(EventType.MARKET_OPEN, lambda event: self._dispatch_market_open(strategy, event))
        self.event_bus.subscribe(EventType.MARKET_CLOSE, lambda event: self._dispatch_market_close(strategy, event))

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
        if self._feed_intraday_bars:
            feed_strategy_bars(strategy, [data])

    def _dispatch_market_open(self, strategy: Any, event: Any) -> None:
        trading_date = self._event_trading_date(event)
        if self._daily_snapshot_mode:
            self._run_completed_daily_snapshot(strategy, trading_date)
        before_trading(strategy, trading_date)

    def _dispatch_market_close(self, strategy: Any, event: Any) -> None:
        trading_date = self._event_trading_date(event)
        if self._daily_snapshot_mode:
            if trading_date in self._daily_bar_buffer:
                self._completed_daily_snapshots.add(trading_date)
            return
        after_trading(strategy, trading_date)

    def _run_completed_daily_snapshot(self, strategy: Any, current_date: date) -> None:
        eligible_dates = [
            trading_date
            for trading_date in self._completed_daily_snapshots
            if trading_date < current_date and not self._strategy_processed_snapshot(strategy, trading_date)
        ]
        if not eligible_dates:
            return
        trading_date = max(eligible_dates)
        for stale_date in eligible_dates:
            if stale_date != trading_date:
                self._daily_snapshot_processed.pop(stale_date, None)
                self._completed_daily_snapshots.discard(stale_date)
                self._daily_bar_buffer.pop(stale_date, None)
        bars = list(self._daily_bar_buffer.get(trading_date, {}).values())
        result = run_daily_snapshot(
            strategy,
            trading_date,
            bars,
            strict=self._strict_daily_snapshot,
        )
        if not result.ran:
            self.logger.warning(
                "Skipped daily snapshot for %s on %s: missing=%s stale=%s",
                getattr(strategy, "name", strategy.__class__.__name__),
                trading_date,
                result.missing_symbols,
                result.stale_symbols,
            )
        self._mark_strategy_processed_snapshot(strategy, trading_date)

    def _strategy_processed_snapshot(self, strategy: Any, trading_date: date) -> bool:
        strategy_name = getattr(strategy, "name", strategy.__class__.__name__)
        return strategy_name in self._daily_snapshot_processed.get(trading_date, set())

    def _mark_strategy_processed_snapshot(self, strategy: Any, trading_date: date) -> None:
        strategy_name = getattr(strategy, "name", strategy.__class__.__name__)
        processed = self._daily_snapshot_processed.setdefault(trading_date, set())
        processed.add(strategy_name)
        expected = {
            getattr(item, "name", item.__class__.__name__)
            for item in self.strategies
        }
        if expected and expected.issubset(processed):
            self._daily_snapshot_processed.pop(trading_date, None)
            self._completed_daily_snapshots.discard(trading_date)
            self._daily_bar_buffer.pop(trading_date, None)

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

        market_config = self.config.get("markets", {}).get("US", {})
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
            for strategy in self.strategies:
                self._run_completed_daily_snapshot(strategy, flush_day)

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
