from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, List

from quant.domain.models.bar import Bar

DataFeedCallback = Callable[[Bar], None]


class DataFeed(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        pass

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
    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> Any:
        pass

    @abstractmethod
    def subscribe(self, symbols: List[str], callback: DataFeedCallback) -> None:
        pass

    @abstractmethod
    def unsubscribe(self, symbols: List[str]) -> None:
        pass
