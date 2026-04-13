"""Base abstract class for broker adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional


class OrderStatus(Enum):
    """Order status enum."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """Represents a trading order."""
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


@dataclass
class Position:
    """Represents a broker position."""
    symbol: str
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float


@dataclass
class AccountInfo:
    """Account information."""
    account_id: str
    cash: float
    buying_power: float
    equity: float
    margin_used: float


class BrokerAdapter(ABC):
    """Abstract base class for broker adapters."""

    def __init__(self, name: str):
        self.name = name
        self._connected = False

    @abstractmethod
    def connect(self) -> None:
        """Connect to the broker."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the broker."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to the broker."""
        pass

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        """
        Submit an order to the broker.
        Returns the broker's order_id.
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order. Returns True if successful."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Get current positions from the broker."""
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """Get account information from the broker."""
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Get the status of an order."""
        pass
