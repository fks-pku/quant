"""Base abstract class for trading strategies."""

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from quant.core.engine import Context

from quant.utils.logger import get_logger


class Strategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, name: str):
        self.name = name
        self.context: Optional["Context"] = None
        self._data: Dict[str, Any] = {}
        self._positions: Dict[str, float] = {}
        self.logger = get_logger(f"Strategy.{name}")

    @property
    def symbols(self) -> List[str]:
        """List of symbols this strategy trades."""
        return []

    def on_start(self, context: "Context") -> None:
        """Called when strategy starts."""
        self.context = context
        self._load_data()

    def on_before_trading(self, context: "Context", trading_date: date) -> None:
        """Called before market opens for the trading date."""
        pass

    def on_data(self, context: "Context", data: Any) -> None:
        """Called on each bar/quote of data."""
        pass

    def on_fill(self, context: "Context", fill: Any) -> None:
        """Called when an order is filled."""
        if hasattr(fill, "symbol") and hasattr(fill, "quantity"):
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + fill.quantity

    def on_order_rejected(self, context: "Context", order: Any, reason: str) -> None:
        """Called when an order is rejected."""
        pass

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        """Called after market closes for the trading date."""
        pass

    def on_stop(self, context: "Context") -> None:
        """Called when strategy stops."""
        self._positions.clear()

    def buy(self, symbol: str, quantity: float, order_type: str = "MARKET", price: Optional[float] = None) -> Optional[str]:
        """Submit a buy order."""
        if self.context and self.context.portfolio:
            order_manager = getattr(self.context, "order_manager", None)
            if order_manager:
                return order_manager.submit_order(symbol, quantity, "BUY", order_type, price, self.name)
        return None

    def sell(self, symbol: str, quantity: float, order_type: str = "MARKET", price: Optional[float] = None) -> Optional[str]:
        """Submit a sell order."""
        if self.context and self.context.portfolio:
            order_manager = getattr(self.context, "order_manager", None)
            if order_manager:
                return order_manager.submit_order(symbol, quantity, "SELL", order_type, price, self.name)
        return None

    def get_position(self, symbol: str) -> float:
        """Get current position for a symbol."""
        return self._positions.get(symbol, 0)

    def get_all_positions(self) -> Dict[str, float]:
        """Get all current positions."""
        return self._positions.copy()

    def _load_data(self) -> None:
        """Load historical data for strategy initialization."""
        pass

    def _store_data(self, key: str, value: Any) -> None:
        """Store strategy-specific data."""
        self._data[key] = value

    def _get_data(self, key: str, default: Any = None) -> Any:
        """Retrieve strategy-specific data."""
        return self._data.get(key, default)
