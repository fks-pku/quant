from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from quant.domain.events.base import Event, EventType
from quant.domain.models.order import Order


@dataclass(frozen=True)
class OrderSubmittedEvent(Event):
    order: Order = None
    broker_order_id: Optional[str] = None

    def __init__(self, order: Order, broker_order_id: Optional[str] = None,
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.ORDER_SUBMITTED, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'order', order)
        object.__setattr__(self, 'broker_order_id', broker_order_id)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["order_id"] = self.order.order_id if self.order else None
        d["symbol"] = self.order.symbol if self.order else None
        d["broker_order_id"] = self.broker_order_id
        return d


@dataclass(frozen=True)
class OrderFilledEvent(Event):
    order: Order = None
    fill_quantity: float = 0.0
    fill_price: float = 0.0
    commission: float = 0.0

    def __init__(self, order: Order, fill_quantity: float, fill_price: float,
                 commission: float = 0.0, source: Optional[str] = None,
                 timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.ORDER_FILLED, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'order', order)
        object.__setattr__(self, 'fill_quantity', fill_quantity)
        object.__setattr__(self, 'fill_price', fill_price)
        object.__setattr__(self, 'commission', commission)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["order_id"] = self.order.order_id if self.order else None
        d["symbol"] = self.order.symbol if self.order else None
        d["fill_quantity"] = self.fill_quantity
        d["fill_price"] = self.fill_price
        d["commission"] = self.commission
        return d


@dataclass(frozen=True)
class OrderCancelledEvent(Event):
    order: Order = None
    reason: str = ""

    def __init__(self, order: Order, reason: str = "",
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.ORDER_CANCELLED, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'order', order)
        object.__setattr__(self, 'reason', reason)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["order_id"] = self.order.order_id if self.order else None
        d["symbol"] = self.order.symbol if self.order else None
        d["reason"] = self.reason
        return d


@dataclass(frozen=True)
class OrderRejectedEvent(Event):
    order: Order = None
    reason: str = ""

    def __init__(self, order: Order, reason: str = "",
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.ORDER_REJECTED, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'order', order)
        object.__setattr__(self, 'reason', reason)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["order_id"] = self.order.order_id if self.order else None
        d["symbol"] = self.order.symbol if self.order else None
        d["reason"] = self.reason
        return d
