"""Fill processing and reconciliation with portfolio updates."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING
import threading

from quant.execution.brokers.base import BrokerAdapter, Order, OrderStatus
from quant.utils.logger import setup_logger

if TYPE_CHECKING:
    from quant.core.portfolio import Portfolio
    from quant.core.events import EventBus


@dataclass
class Fill:
    """Represents a trade fill."""
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    timestamp: datetime


class FillHandler:
    """
    Reconciles broker fills with local order state.
    Updates portfolio positions.
    Triggers strategy callbacks on fill events.
    """

    def __init__(
        self,
        portfolio: "Portfolio",
        event_bus: "EventBus",
        config: Dict[str, Any],
    ):
        self.portfolio = portfolio
        self.event_bus = event_bus
        self.config = config

        self._fills: List[Fill] = []
        self._fill_callbacks: List[Callable] = []
        self._lock = threading.RLock()
        self.logger = setup_logger("FillHandler")

    def register_fill_callback(self, callback: Callable) -> None:
        """Register a callback to be called on each fill."""
        self._fill_callbacks.append(callback)

    def process_fill(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        commission: float = 0.0,
        timestamp: Optional[datetime] = None,
    ) -> Fill:
        """
        Process a fill and update portfolio.
        Returns the Fill object.
        """
        if timestamp is None:
            timestamp = datetime.now()

        fill = Fill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            timestamp=timestamp,
        )

        with self._lock:
            self._fills.append(fill)

        self._update_portfolio(fill)
        self._notify_callbacks(fill)
        self._publish_fill_event(fill)

        self.logger.info(
            f"Fill processed: {order_id} {side} {quantity} {symbol} @ {price:.2f}"
        )

        return fill

    def _update_portfolio(self, fill: Fill) -> None:
        """Update portfolio positions based on fill."""
        if fill.side.upper() == "BUY":
            cost = fill.price * fill.quantity + fill.commission
            self.portfolio.update_position(
                symbol=fill.symbol,
                quantity=fill.quantity,
                price=fill.price,
                cost=cost,
            )
        elif fill.side.upper() == "SELL":
            self.portfolio.close_position(fill.symbol, fill.price)

    def _notify_callbacks(self, fill: Fill) -> None:
        """Notify registered callbacks of the fill."""
        for callback in self._fill_callbacks:
            try:
                callback(fill)
            except Exception as e:
                self.logger.error(f"Error in fill callback: {e}")

    def _publish_fill_event(self, fill: Fill) -> None:
        """Publish fill event to the event bus."""
        from quant.core.events import EventType

        self.event_bus.publish_nowait(
            EventType.ORDER_FILL,
            {
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side,
                "quantity": fill.quantity,
                "price": fill.price,
                "commission": fill.commission,
                "timestamp": fill.timestamp,
            }
        )

    def get_fills(
        self,
        symbol: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Fill]:
        """Get fill history, optionally filtered."""
        with self._lock:
            fills = self._fills.copy()

        if symbol:
            fills = [f for f in fills if f.symbol == symbol]
        if start:
            fills = [f for f in fills if f.timestamp >= start]
        if end:
            fills = [f for f in fills if f.timestamp <= end]

        return fills

    def get_total_commission(self) -> float:
        """Get total commission paid."""
        with self._lock:
            return sum(f.commission for f in self._fills)

    def get_fill_stats(self) -> Dict[str, Any]:
        """Get fill statistics."""
        with self._lock:
            total_fills = len(self._fills)
            buy_fills = [f for f in self._fills if f.side.upper() == "BUY"]
            sell_fills = [f for f in self._fills if f.side.upper() == "SELL"]
            total_volume = sum(f.quantity * f.price for f in self._fills)
            total_commission = sum(f.commission for f in self._fills)

            return {
                "total_fills": total_fills,
                "buy_fills": len(buy_fills),
                "sell_fills": len(sell_fills),
                "total_volume": total_volume,
                "total_commission": total_commission,
            }
