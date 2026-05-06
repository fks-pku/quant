from abc import ABC, abstractmethod
from typing import Any, List


class ResearchMarketData(ABC):
    @abstractmethod
    def get_daily_bars(self, symbols: List[str], start: str, end: str) -> Any:
        raise NotImplementedError
