"""Tushare Pro data provider for China A-share stock and index daily bars.

Implements the domain DataFeed port, fetching data from Tushare Pro API
and caching all results via the Storage port.
"""

import time
from datetime import datetime
from typing import Optional

import pandas as pd

from quant.infrastructure.data.providers.base import DataProvider
from quant.domain.ports.storage import Storage
from quant.infrastructure.data.storage_duckdb import DuckDBStorage, _DEFAULT_DB
from quant.shared.utils.config_loader import ConfigLoader
from quant.shared.utils.logger import setup_logger

try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    ts = None
    TUSHARE_AVAILABLE = False

_CN_INDEX_CODES = {
    "000001", "000016", "000300", "000905",
    "399001", "399006", "399673",
}


class TushareProvider(DataProvider):
    def __init__(self, db_path: str = _DEFAULT_DB, min_interval: float = 0.3):
        super().__init__("tushare")
        self._db_path = db_path
        self._min_interval = min_interval
        self._last_request_time = 0.0
        self._api = None
        self._storage: Optional[Storage] = None
        self.logger = setup_logger("TushareProvider")

    def _load_config(self) -> dict:
        try:
            loader = ConfigLoader()
            cfg = loader.load("config.yaml")
            tushare_cfg = cfg.get("data", {}).get("tushare", {})
            return {
                "token": tushare_cfg.get("token", ""),
                "api_url": tushare_cfg.get("api_url", ""),
            }
        except Exception:
            return {"token": "", "api_url": ""}

    def connect(self) -> None:
        if not TUSHARE_AVAILABLE:
            self.logger.warning("tushare not installed")
            self._connected = True
            return

        cfg = self._load_config()
        token = cfg.get("token", "")
        if not token:
            self.logger.warning("tushare token not configured")
            self._connected = True
            return

        self._api = ts.pro_api(token=token)
        api_url = cfg.get("api_url", "")
        if api_url:
            self._api._DataApi__http_url = api_url

        self._storage = DuckDBStorage(self._db_path)
        self._connected = True
        self.logger.info("TushareProvider connected")

    def disconnect(self) -> None:
        if self._storage:
            self._storage.close()
            self._storage = None
        self._api = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    @staticmethod
    def _is_index(symbol: str) -> bool:
        return symbol in _CN_INDEX_CODES

    @staticmethod
    def _to_ts_code(symbol: str) -> str:
        if TushareProvider._is_index(symbol):
            if symbol.startswith("399"):
                return f"{symbol}.SZ"
            return f"{symbol}.SH"
        if symbol[0] in ("6", "9"):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    @staticmethod
    def _from_ts_code(ts_code: str) -> str:
        return ts_code.split(".")[0]

    def _fetch_daily(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        if self._api is None:
            return pd.DataFrame()

        ts_code = self._to_ts_code(symbol)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        self._rate_limit()

        try:
            if self._is_index(symbol):
                df = self._api.index_daily(
                    ts_code=ts_code, start_date=start_str, end_date=end_str,
                )
            else:
                df = self._api.daily(
                    ts_code=ts_code, start_date=start_str, end_date=end_str,
                )
        except Exception as e:
            self.logger.warning(f"Error fetching bars for {symbol}: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        return self._normalize_bars(df, symbol)

    def _normalize_bars(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()

        col_mapping = {
            "trade_date": "timestamp",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "vol": "volume",
            "amount": "turnover",
        }

        df = df.rename(columns={k: v for k, v in col_mapping.items() if k in df.columns})

        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y%m%d")

        df["symbol"] = symbol

        desired = ["timestamp", "symbol", "open", "high", "low", "close", "volume", "turnover"]
        available = [c for c in desired if c in df.columns]
        df = df[available]

        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=[c for c in ["open", "high", "low", "close"] if c in df.columns])
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        if not self._connected:
            self.connect()

        if timeframe not in ("1d", "day", "daily"):
            self.logger.warning(f"tushare only supports daily bars, got {timeframe}")
            return pd.DataFrame()

        if self._storage is not None:
            cached = self._storage.get_bars(symbol, start, end, timeframe)
            if not cached.empty:
                cached_start = cached["timestamp"].min().to_pydatetime()
                cached_end = cached["timestamp"].max().to_pydatetime()
                if cached_start <= start and cached_end >= end:
                    return cached

                missing_start = start
                missing_end = end
                if cached_start <= start:
                    missing_start = cached_end
                if cached_end >= end:
                    missing_end = cached_start

                if missing_start < missing_end:
                    fresh = self._fetch_daily(symbol, missing_start, missing_end)
                    if not fresh.empty and self._storage is not None:
                        self._storage.save_bars(fresh, timeframe)
                    if not cached.empty and not fresh.empty:
                        return pd.concat([cached, fresh]).drop_duplicates(
                            subset=["timestamp", "symbol"],
                        ).sort_values("timestamp").reset_index(drop=True)
                return cached

        fresh = self._fetch_daily(symbol, start, end)
        if not fresh.empty and self._storage is not None:
            self._storage.save_bars(fresh, timeframe)
        return fresh

    def get_quote(self, symbol: str) -> dict:
        if not self._connected:
            self.connect()

        if not TUSHARE_AVAILABLE or self._api is None:
            self.logger.error("tushare not available")
            return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}

        today = datetime.now()
        start = today.replace(year=today.year - 1)

        self._rate_limit()
        try:
            df = self.get_bars(symbol, start, today, "1d")
            if df.empty:
                return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}

            last = df.iloc[-1]
            price = float(last["close"])
            ts = last["timestamp"] if "timestamp" in df.columns else None
            return {
                "timestamp": ts,
                "symbol": symbol,
                "bid": price,
                "ask": price,
                "bid_size": 0,
                "ask_size": 0,
            }
        except Exception as e:
            self.logger.warning(f"Error fetching quote for {symbol}: {e}")
            return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}
