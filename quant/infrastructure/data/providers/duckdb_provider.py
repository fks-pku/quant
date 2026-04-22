"""DuckDB-backed data provider for backtesting.

Implements the DataProvider ABC interface, reading bars from DuckDB tables.
Used as the unified data source for all backtests.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from quant.infrastructure.data.providers.base import DataProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage, _DEFAULT_DB
from quant.shared.utils.logger import setup_logger


class DuckDBProvider(DataProvider):
    def __init__(self, db_path: str = _DEFAULT_DB):
        super().__init__("DuckDB")
        self._db_path = db_path
        self._storage: Optional[DuckDBStorage] = None
        self.logger = setup_logger("DuckDBProvider")

    def connect(self) -> None:
        self._storage = DuckDBStorage(self._db_path, read_only=True)
        self._connected = True
        tables = self._storage.list_tables()
        self.logger.info(f"Connected to DuckDB (read-only), tables: {tables}")

    def disconnect(self) -> None:
        if self._storage:
            self._storage.close()
            self._storage = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._storage is not None

    def get_bars(self, symbol: str, start: datetime, end: datetime, timeframe: str = "1d") -> pd.DataFrame:
        if not self.is_connected():
            raise RuntimeError("DuckDBProvider not connected")
        return self._storage.get_bars(symbol, start, end, timeframe)

    def get_quote(self, symbol: str) -> dict:
        if not self.is_connected():
            raise RuntimeError("DuckDBProvider not connected")
        df = self._storage.get_bars(symbol, datetime.now(), datetime.now(), "1d")
        if df.empty:
            return {"timestamp": None, "symbol": symbol, "bid": 0, "ask": 0, "bid_size": 0, "ask_size": 0}
        last = df.iloc[-1]
        price = float(last.get("close", 0))
        return {
            "timestamp": last.get("timestamp"),
            "symbol": symbol,
            "bid": price,
            "ask": price,
            "bid_size": 0,
            "ask_size": 0,
        }

    @property
    def storage(self) -> DuckDBStorage:
        if self._storage is None:
            raise RuntimeError("Not connected")
        return self._storage

    def list_available_symbols(self, timeframe: str = "1d", market: str = "hk") -> List[str]:
        if self._storage is None:
            return []
        return self._storage.get_symbols(timeframe, market)

    def get_available_range(self, symbol: str, timeframe: str = "1d") -> Optional[Dict[str, datetime]]:
        if self._storage is None:
            return None
        return self._storage.get_date_range(symbol, timeframe)
