from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Order:
    symbol: str
    quantity: float
    side: OrderSide
    order_type: OrderType
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    timestamp: Optional[datetime] = None
    strategy_name: Optional[str] = None

    @property
    def is_buy(self) -> bool:
        return self.side == OrderSide.BUY

    @property
    def is_sell(self) -> bool:
        return self.side == OrderSide.SELL

    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL)

    def with_fill(self, fill_quantity: float, fill_price: float) -> "Order":
        new_filled = self.filled_quantity + fill_quantity
        if self.avg_fill_price and self.filled_quantity > 0:
            total_cost = self.avg_fill_price * self.filled_quantity + fill_price * fill_quantity
            new_avg = total_cost / new_filled
        else:
            new_avg = fill_price
        new_status = OrderStatus.FILLED if new_filled >= self.quantity else OrderStatus.PARTIAL
        return replace(
            self,
            filled_quantity=new_filled,
            avg_fill_price=new_avg,
            status=new_status,
        )

    def with_status(self, status: OrderStatus) -> "Order":
        return replace(self, status=status)
