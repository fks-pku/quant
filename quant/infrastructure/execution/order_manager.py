"""Order lifecycle management with routing and retry logic."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import threading
import time
import uuid

from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.domain.ports.broker import BrokerAdapter
from quant.domain.ports.event_publisher import EventPublisher
from quant.domain.events.base import EventType, Event
from quant.shared.utils.logger import setup_logger


@dataclass
class OrderRequest:
    symbol: str
    quantity: float
    side: str
    order_type: str
    price: Optional[float] = None
    strategy_name: Optional[str] = None
    order_id: Optional[str] = None


class OrderManager:

    def __init__(
        self,
        portfolio: Any,
        risk_engine: Any,
        event_bus: EventPublisher,
        config: Dict[str, Any],
        strategy_tracker: Any = None,
    ):
        self.portfolio = portfolio
        self.risk_engine = risk_engine
        self.event_bus = event_bus
        self.config = config
        self._strategy_tracker = strategy_tracker

        self._brokers: Dict[str, BrokerAdapter] = {}
        self._orders: Dict[str, Order] = {}
        self._symbol_to_broker: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._max_retries = 3
        self._retry_delay = 1.0
        self.logger = setup_logger("OrderManager")

    def register_broker(self, name: str, broker: BrokerAdapter, symbols: Optional[List[str]] = None) -> None:
        """Register a broker adapter for order routing."""
        self._brokers[name] = broker
        if symbols:
            for symbol in symbols:
                self._symbol_to_broker[symbol] = name
        self.logger.info(f"Registered broker: {name}")

    def get_broker_for_symbol(self, symbol: str) -> BrokerAdapter:
        """Get the appropriate broker for a symbol."""
        broker_name = self._symbol_to_broker.get(symbol, "paper")
        broker = self._brokers.get(broker_name) or self._brokers.get("paper")
        if broker is None:
            raise RuntimeError("No broker available")
        return broker

    def submit_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        strategy_name: Optional[str] = None,
    ) -> Optional[str]:
        """
        Submit an order after risk checks.
        Returns order_id if successful, None if rejected.
        """
        order_value = abs(quantity * (price or 0))
        if price is None:
            price = self._get_last_price(symbol)
            order_value = abs(quantity * price)

        approved, results = self.risk_engine.check_order(
            symbol=symbol,
            quantity=quantity,
            price=price,
            order_value=order_value,
        )

        self.risk_engine.log_result(results)

        if not approved:
            self.logger.warning(f"Order rejected by risk engine: {symbol} {side} {quantity}")
            self.event_bus.publish_nowait(
                EventType.ORDER_REJECTED,
                {
                    "symbol": symbol,
                    "quantity": quantity,
                    "side": side,
                    "reason": "risk_check_failed",
                }
            )
            return None

        order_id = str(uuid.uuid4())[:12].upper()

        order = Order(
            symbol=symbol,
            quantity=quantity,
            side=OrderSide(side),
            order_type=OrderType(order_type),
            order_id=order_id,
            status=OrderStatus.PENDING,
            price=price,
            timestamp=datetime.now(),
            strategy_name=strategy_name,
        )

        with self._lock:
            self._orders[order_id] = order

        self._record_strategy(order_id, strategy_name)
        self.risk_engine.record_order()
        self._submit_to_broker(order)

        return order_id

    def _submit_to_broker(self, order: Order) -> None:
        """Submit order to broker with retry logic."""
        broker = self.get_broker_for_symbol(order.symbol)

        for attempt in range(self._max_retries):
            try:
                broker_order_id = broker.submit_order(order)
                updated = replace(order, order_id=broker_order_id, status=OrderStatus.SUBMITTED)
                with self._lock:
                    if order.order_id:
                        self._orders[order.order_id] = updated
                    self._orders[broker_order_id] = updated

                self.logger.info(f"Order submitted: {broker_order_id} {order.symbol} {order.side} {order.quantity}")

                self.event_bus.publish_nowait(
                    EventType.ORDER_SUBMITTED,
                    {
                        "order_id": broker_order_id,
                        "symbol": order.symbol,
                        "quantity": order.quantity,
                        "side": order.side.value if hasattr(order.side, 'value') else order.side,
                    }
                )
                return

            except Exception as e:
                self.logger.warning(f"Order submission attempt {attempt + 1} failed: {e}")
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (2 ** attempt))

        rejected = replace(order, status=OrderStatus.REJECTED)
        with self._lock:
            self._orders[order.order_id] = rejected
        self.logger.error(f"Order rejected after {self._max_retries} attempts: {order.symbol}")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        with self._lock:
            if order_id not in self._orders:
                return False

            order = self._orders[order_id]
            broker = self.get_broker_for_symbol(order.symbol)

            try:
                success = broker.cancel_order(order.order_id or order_id)
                if success:
                    self._orders[order_id] = replace(order, status=OrderStatus.CANCELLED)
                    self.logger.info(f"Order cancelled: {order_id}")
                    return True
            except Exception as e:
                self.logger.error(f"Failed to cancel order {order_id}: {e}")

            return False

    def get_order_status(self, order_id: str) -> Optional[OrderStatus]:
        """Get the current status of an order."""
        with self._lock:
            order = self._orders.get(order_id)
            if order:
                return order.status
            return None

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        with self._lock:
            return self._orders.get(order_id)

    def get_all_orders(self) -> List[Order]:
        """Get all orders."""
        with self._lock:
            seen = set()
            result = []
            for o in self._orders.values():
                if o.order_id not in seen:
                    seen.add(o.order_id)
                    result.append(o)
            return result

    def get_open_orders(self) -> List[Order]:
        """Get all open (pending/submitted) orders."""
        with self._lock:
            return [
                o for o in self._orders.values()
                if o.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED)
            ]

    def update_order_from_fill(
        self,
        order_id: str,
        filled_quantity: float,
        avg_fill_price: float,
    ) -> None:
        """Update order based on fill data."""
        with self._lock:
            order = self._orders.get(order_id)
            if not order:
                return

            if filled_quantity >= order.quantity:
                new_status = OrderStatus.FILLED
            else:
                new_status = OrderStatus.PARTIAL

            self._orders[order_id] = replace(
                order,
                filled_quantity=filled_quantity,
                avg_fill_price=avg_fill_price,
                status=new_status,
            )

    def _get_last_price(self, symbol: str) -> float:
        """Get last known price for a symbol (placeholder)."""
        return 100.0

    def _record_strategy(self, order_id: str, strategy_name: Optional[str]) -> None:
        if self._strategy_tracker is None:
            return
        try:
            self._strategy_tracker.record_order(order_id, strategy_name)
        except Exception:
            pass
