from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional


class Storage(ABC):

    @abstractmethod
    def save_bars(self, df: Any, timeframe: str = "1d") -> int:
        pass

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        timeframe: str = "1d",
    ) -> Any:
        pass

    @abstractmethod
    def get_symbols(self, timeframe: str = "1d", market: str = "hk") -> List[str]:
        pass

    @abstractmethod
    def get_date_range(self, symbol: str, timeframe: str = "1d") -> Optional[Dict[str, datetime]]:
        pass

    @abstractmethod
    def get_lot_size(self, symbol: str) -> int:
        pass

    @abstractmethod
    def save_order(self, order: dict) -> None:
        pass

    @abstractmethod
    def get_orders(self, symbol: Optional[str] = None, status: Optional[str] = None) -> Any:
        pass

    @abstractmethod
    def save_portfolio_snapshot(self, snapshot: dict) -> None:
        pass

    @abstractmethod
    def get_portfolio_snapshots(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Any:
        pass

    @abstractmethod
    def save_strategy_snapshot(self, snapshot: dict) -> None:
        pass

    @abstractmethod
    def get_strategy_snapshots(self, strategy_name: Optional[str] = None) -> List[dict]:
        pass

    @abstractmethod
    def list_tables(self) -> List[str]:
        pass

    @abstractmethod
    def table_row_count(self, table_name: str) -> int:
        pass

    @abstractmethod
    def close(self) -> None:
        pass
