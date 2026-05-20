"""Base abstract class for trading strategies."""

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

if TYPE_CHECKING:
    from quant.domain.context import StrategyContext as Context

from quant.shared.utils.logger import get_logger
from quant.domain.exceptions import OrderRejectedError


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

    def on_data_batch(self, context: "Context", data: Iterable[Any]) -> None:
        """Called with all bars for one trading step."""
        bars = data.values() if isinstance(data, dict) else data
        for bar in bars:
            self.on_data(context, bar)

    def on_fill(self, context: "Context", fill: Any) -> None:
        """Called when an order is filled."""
        if hasattr(fill, "symbol") and hasattr(fill, "quantity"):
            qty = fill.quantity
            if hasattr(fill, "side") and fill.side == "SELL":
                qty = -qty
            self._positions[fill.symbol] = self._positions.get(fill.symbol, 0) + qty

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
        """Submit a buy order. Returns None if rejected."""
        if self.context and hasattr(self.context, "submit_order"):
            try:
                return self.context.submit_order(symbol, quantity, "BUY", order_type, price, self.name)
            except OrderRejectedError:
                return None
        return None

    def sell(self, symbol: str, quantity: float, order_type: str = "MARKET", price: Optional[float] = None) -> Optional[str]:
        """Submit a sell order. Returns None if rejected."""
        if self.context and hasattr(self.context, "submit_order"):
            try:
                return self.context.submit_order(symbol, quantity, "SELL", order_type, price, self.name)
            except OrderRejectedError:
                return None
        return None

    def get_position(self, symbol: str) -> float:
        """Get current position for a symbol."""
        return self._positions.get(symbol, 0)

    def get_all_positions(self) -> Dict[str, float]:
        """Get all current positions."""
        return self._positions.copy()

    @staticmethod
    def _adj(bar, field: str = "close", default: float = 0.0) -> float:
        """返回后复权价格，用于信号/技术指标计算。不要用于计算下单量。"""
        if isinstance(bar, dict):
            v = bar.get(f"adj_{field}")
            if v is not None and v == v:
                return float(v)
            return float(bar.get(field, default))
        v = getattr(bar, f"adj_{field}", None)
        if v is not None and v == v:
            return float(v)
        return float(getattr(bar, field, default))

    @staticmethod
    def _price(bar, default: float = 0.0) -> float:
        """返回真实收盘价，用于计算下单量/资金分配。"""
        if isinstance(bar, dict):
            v = bar.get("close")
            return float(v) if v is not None and v == v else default
        v = getattr(bar, "close", None)
        return float(v) if v is not None and v == v else default

    def _load_data(self) -> None:
        """Load historical data for strategy initialization."""
        pass

    def _store_data(self, key: str, value: Any) -> None:
        """Store strategy-specific data."""
        self._data[key] = value

    def _get_data(self, key: str, default: Any = None) -> Any:
        """Retrieve strategy-specific data."""
        return self._data.get(key, default)
