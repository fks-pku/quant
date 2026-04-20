"""Base abstract class for data providers."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, List, Optional
import pandas as pd


class DataProvider(ABC):
    """Abstract base class for data providers."""

    def __init__(self, name: str):
        self.name = name
        self._connected = False

    @abstractmethod
    def connect(self) -> None:
        """Connect to the data provider."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the data provider."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to the provider."""
        pass

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str,
    ) -> pd.DataFrame:
        """
        Get historical bars for a symbol.

        Returns DataFrame with columns:
        timestamp, symbol, open, high, low, close, volume
        """
        pass

    @abstractmethod
    def get_quote(self, symbol: str) -> dict:
        """
        Get current quote for a symbol.

        Returns dict with:
        timestamp, symbol, bid, ask, bid_size, ask_size
        """
        pass

    def subscribe(self, symbols: List[str], callback: Callable) -> None:
        """Subscribe to real-time data for symbols."""
        raise NotImplementedError(f"{self.name} does not support streaming")

    def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from real-time data."""
        raise NotImplementedError(f"{self.name} does not support streaming")
