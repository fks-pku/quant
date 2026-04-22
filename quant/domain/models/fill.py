from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    commission: float
    timestamp: datetime
    fill_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy_name: Optional[str] = None

    @property
    def value(self) -> float:
        return self.quantity * self.price

    @property
    def net_value(self) -> float:
        return self.value - self.commission
