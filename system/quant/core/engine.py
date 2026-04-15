"""Main event loop and orchestration engine."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
import threading
import time

from quant.core.events import EventBus, Event, EventType
from quant.core.scheduler import Scheduler
from quant.core.portfolio import Portfolio
from quant.core.risk import RiskEngine
from quant.utils.logger import setup_logger


class SystemMode(Enum):
    """System operation modes."""
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


@dataclass
class Order:
    """Represents a trading order."""
    symbol: str
    quantity: float
    order_type: str
    side: str
    price: Optional[float] = None


@dataclass
class Context:
    """Strategy context providing access to system components."""
    portfolio: Portfolio
    risk_engine: RiskEngine
    event_bus: EventBus
    order_manager: Any
    data_provider: Any
    broker: Any


class Engine:
    """Main event loop and orchestration engine."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.mode = SystemMode(config.get("system", {}).get("mode", "paper"))
        self.logger = setup_logger("Engine", config.get("system", {}).get("log_level", "INFO"))

        self.event_bus = EventBus()
        self.portfolio = Portfolio(
            initial_cash=config.get("system", {}).get("initial_cash", 100000),
            currency=config.get("system", {}).get("currency", "USD"),
        )
        self.risk_engine = RiskEngine(config, self.portfolio, self.event_bus)
        self.scheduler = Scheduler(config, self.event_bus)
        self.order_manager = None

        self.strategies: List[Any] = []
        self.data_providers: Dict[str, Any] = {}
        self.broker: Optional[Any] = None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._subscribed_symbols: List[str] = []

    def set_data_provider(self, name: str, provider: Any) -> None:
        """Set a data provider."""
        self.data_providers[name] = provider

    def set_broker(self, broker: Any) -> None:
        """Set the broker adapter."""
        self.broker = broker

    def set_order_manager(self, order_manager: Any) -> None:
        """Set the order manager."""
        self.order_manager = order_manager

    def add_strategy(self, strategy: Any) -> None:
        """Add a strategy to the engine."""
        strategy.context = Context(
            portfolio=self.portfolio,
            risk_engine=self.risk_engine,
            event_bus=self.event_bus,
            order_manager=self.order_manager,
            data_provider=self.data_providers.get("default"),
            broker=self.broker,
        )
        self.strategies.append(strategy)

    def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to symbols for real-time data."""
        self._subscribed_symbols.extend(symbols)
        for name, provider in self.data_providers.items():
            if hasattr(provider, "subscribe"):
                provider.subscribe(symbols, lambda data, src=name: self._on_data(src, data))

    def _on_data(self, provider_name: str, data: Any) -> None:
        """Handle incoming data from providers."""
        self.event_bus.publish_nowait(EventType.BAR, data, provider_name)

    def start(self) -> None:
        """Start the engine in a background thread."""
        if self._running:
            return

        self.logger.info(f"Starting engine in {self.mode.value} mode")
        self._running = True

        for strategy in self.strategies:
            if hasattr(strategy, "on_start"):
                strategy.on_start(strategy.context)

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
            if hasattr(strategy, "on_stop"):
                strategy.on_stop(strategy.context)

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
        from quant.utils.datetime_utils import get_current_time, is_market_open

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
            if hasattr(strategy, "on_start"):
                strategy.on_start(strategy.context)

        current_date = start_date
        while current_date <= end_date:
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

            current_date = current_date + timedelta(days=1)

        for strategy in self.strategies:
            if hasattr(strategy, "on_stop"):
                strategy.on_stop(strategy.context)

    def get_portfolio_status(self) -> Dict[str, Any]:
        """Get current portfolio status."""
        return self.portfolio.to_dict()
