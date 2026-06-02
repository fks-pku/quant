"""DuckDB-backed data provider for backtesting.

Implements the DataProvider ABC interface, reading bars from DuckDB tables.
Used as the unified data source for all backtests.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from quant.domain.ports.data_feed import DataFeed
from quant.infrastructure.data.storage_duckdb import DuckDBStorage, _DEFAULT_DB, _DEFAULT_STATUS_DB
from quant.shared.utils.logger import setup_logger


class DuckDBProvider(DataFeed):
    def __init__(
        self,
        db_path: str = _DEFAULT_DB,
        use_security_status: bool = True,
        status_db_path: str = _DEFAULT_STATUS_DB,
        parquet_lake_root: Optional[str] = None,
        prefer_parquet_lake: Optional[bool] = None,
    ):
        self._connected = False
        self._db_path = db_path
        self._use_security_status = use_security_status
        self._status_db_path = status_db_path
        self._parquet_lake_root = parquet_lake_root
        self._prefer_parquet_lake = prefer_parquet_lake
        self._storage: Optional[DuckDBStorage] = None
        self.logger = setup_logger("DuckDBProvider")

    @property
    def name(self) -> str:
        return "DuckDB"

    def connect(self) -> None:
        self._storage = DuckDBStorage(
            self._db_path,
            read_only=True,
            use_security_status=self._use_security_status,
            status_db_path=self._status_db_path,
            parquet_lake_root=self._parquet_lake_root,
            prefer_parquet_lake=self._prefer_parquet_lake,
        )
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

    def get_bars_for_symbols(
        self,
        symbols: List[str],
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        if not self.is_connected():
            raise RuntimeError("DuckDBProvider not connected")
        return self._storage.get_bars_for_symbols(symbols, start, end, timeframe)

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

    def subscribe(self, symbols: list, callback) -> None:
        self.logger.warning("DuckDBProvider does not support real-time subscriptions")

    def unsubscribe(self, symbols: list) -> None:
        pass
