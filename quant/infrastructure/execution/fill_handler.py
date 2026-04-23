"""Fill processing and reconciliation with portfolio updates."""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import threading

from quant.domain.models.fill import Fill
from quant.domain.models.order import OrderStatus
from quant.domain.ports.event_publisher import EventPublisher
from quant.shared.utils.logger import setup_logger


class FillHandler:

    def __init__(
        self,
        portfolio: Any,
        event_bus: EventPublisher,
        config: Dict[str, Any],
        strategy_tracker: Any = None,
    ):
        self.portfolio = portfolio
        self.event_bus = event_bus
        self.config = config
        self._strategy_tracker = strategy_tracker

        self._fills: List[Fill] = []
        self._fill_callbacks: List[Callable] = []
        self._lock = threading.RLock()
        self.logger = setup_logger("FillHandler")

    def register_fill_callback(self, callback: Callable) -> None:
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
        strategy_name: Optional[str] = None,
    ) -> Fill:
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
            strategy_name=strategy_name,
        )

        self._update_tracker(fill)

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
            pos = self.portfolio.get_position(fill.symbol)
            sell_qty = min(fill.quantity, pos.quantity if pos else 0)
            if sell_qty <= 0:
                return
            proceeds = fill.price * sell_qty - fill.commission
            self.portfolio.update_position(
                symbol=fill.symbol,
                quantity=-sell_qty,
                price=fill.price,
                cost=0,
            )
            self.portfolio.cash += proceeds

    def _notify_callbacks(self, fill: Fill) -> None:
        """Notify registered callbacks of the fill."""
        for callback in self._fill_callbacks:
            try:
                callback(fill)
            except Exception as e:
                self.logger.error(f"Error in fill callback: {e}")

    def _publish_fill_event(self, fill: Fill) -> None:
        from quant.domain.events.base import EventType

        if self.event_bus:
            self.event_bus.publish_nowait(EventType.ORDER_FILLED, {
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side,
                "quantity": fill.quantity,
                "price": fill.price,
                "commission": fill.commission,
                "timestamp": fill.timestamp,
                "strategy_name": fill.strategy_name,
            })

    def _update_tracker(self, fill: Fill) -> None:
        if self._strategy_tracker is None:
            return
        try:
            strategy = fill.strategy_name or self._strategy_tracker.get_strategy_for_order(fill.order_id)
            self._strategy_tracker.update_from_fill(
                strategy_name=strategy,
                symbol=fill.symbol,
                side=fill.side,
                qty=fill.quantity,
                price=fill.price,
            )
        except Exception as e:
            self.logger.error(f"Failed to update strategy tracker: {e}")

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
