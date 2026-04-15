"""Canonical Order model."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """Canonical order representation used across the entire system."""

    symbol: str
    quantity: float
    side: str
    order_type: str
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    price: Optional[float] = None
    filled_quantity: float = 0
    avg_fill_price: Optional[float] = None
    timestamp: Optional[datetime] = None
    strategy_name: Optional[str] = None
