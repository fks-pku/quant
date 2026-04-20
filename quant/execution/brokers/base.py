"""Base abstract class for broker adapters."""

from abc import ABC, abstractmethod
from typing import List

from quant.models import Order, OrderStatus, Position, AccountInfo


__all__ = ["BrokerAdapter", "Order", "OrderStatus", "Position", "AccountInfo"]


class BrokerAdapter(ABC):
    """Abstract base class for broker adapters."""

    def __init__(self, name: str):
        self.name = name
        self._connected = False

    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        pass
