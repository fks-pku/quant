"""Canonical Fill model."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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
    strategy_name: Optional[str] = None
