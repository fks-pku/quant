from abc import ABC, abstractmethod
from typing import List

from quant.domain.models.order import Order, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.account import AccountInfo


class BrokerAdapter(ABC):

    def __init__(self, name: str):
        self._name = name
        self._connected = False

    @property
    def name(self) -> str:
        return self._name

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
    def get_order_status(self, order_id: str) -> OrderStatus:
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        pass

    def update_price(self, symbol: str, price: float) -> None:
        pass
