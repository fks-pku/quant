from abc import ABC, abstractmethod
from typing import Any, List


class PITData(ABC):
    @abstractmethod
    def get_universe(self, as_of_date: str, market: str) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def get_bars_pit(self, symbols: List[str], start: str, end: str, as_of_date: str) -> Any:
        raise NotImplementedError
