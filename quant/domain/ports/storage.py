from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

import pandas as pd

from quant.domain.models.order import Order


class Storage(ABC):

    @abstractmethod
    def save_bars(self, symbol: str, df: pd.DataFrame) -> None:
        pass

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        pass

    @abstractmethod
    def save_order(self, order: Order) -> None:
        pass

    @abstractmethod
    def get_orders(
        self,
        symbol: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Order]:
        pass

    @abstractmethod
    def save_portfolio_snapshot(self, snapshot: dict) -> None:
        pass

    @abstractmethod
    def get_portfolio_snapshots(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[dict]:
        pass

    @abstractmethod
    def close(self) -> None:
        pass
