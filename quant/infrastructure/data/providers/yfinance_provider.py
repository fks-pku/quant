import os
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import pandas as pd

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

from quant.infrastructure.data.providers.base import DataProvider
from quant.shared.utils.logger import setup_logger

_PKG_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CACHE = str(_PKG_DIR / "var" / "cache")


class YfinanceProvider(DataProvider):
    def __init__(self, cache_dir: str = _DEFAULT_CACHE, cache_ttl_hours: int = 24):
        super().__init__("yfinance")
        self._cache_dir = cache_dir
        self._cache_ttl_hours = cache_ttl_hours
        self._last_download_time = 0.0
        self._min_download_interval = 0.5
        self.logger = setup_logger("YfinanceProvider")
        os.makedirs(cache_dir, exist_ok=True)

    def connect(self) -> None:
        if not YF_AVAILABLE:
            self.logger.warning("yfinance not installed")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _sanitize_symbol(self, symbol: str) -> str:
        return symbol.replace("^", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")

    def _cache_path(self, symbol: str, start: datetime, end: datetime, timeframe: str) -> str:
        safe = self._sanitize_symbol(symbol)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")
        filename = f"{safe}_{start_str}_{end_str}_{timeframe}.parquet"
        return os.path.join(self._cache_dir, filename)

    def _cache_valid(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        age_hours = (time.time() - os.path.getmtime(path)) / 3600
        return age_hours < self._cache_ttl_hours

    def _rate_limit(self):
        elapsed = time.time() - self._last_download_time
        if elapsed < self._min_download_interval:
            time.sleep(self._min_download_interval - elapsed)
        self._last_download_time = time.time()

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        if not self._connected:
            self.connect()

        if not YF_AVAILABLE:
            self.logger.error("yfinance not installed")
            return pd.DataFrame()

        cache_file = self._cache_path(symbol, start, end, timeframe)

        if self._cache_valid(cache_file):
            try:
                df = pd.read_parquet(cache_file)
                return df
            except Exception as e:
                self.logger.warning(f"Cache read error for {symbol}: {e}")

        self._rate_limit()

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=timeframe,
                auto_adjust=True,
            )

            if df.empty:
                return pd.DataFrame()

            df = df.reset_index()
            df.columns = [col.lower() for col in df.columns]

            col_rename = {}
            if "index" in df.columns:
                col_rename["index"] = "timestamp"
            elif "date" in df.columns:
                col_rename["date"] = "timestamp"
            elif "datetime" in df.columns:
                col_rename["datetime"] = "timestamp"
            df = df.rename(columns=col_rename)

            df["symbol"] = symbol

            drop_cols = [c for c in df.columns if c.strip() in ("dividends", "stock splits")]
            if drop_cols:
                df = df.drop(columns=drop_cols)

            available = [c for c in ["timestamp", "symbol", "open", "high", "low", "close", "volume"] if c in df.columns]
            df = df[available]

            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                df.to_parquet(cache_file)
            except Exception as e:
                self.logger.warning(f"Cache write error for {symbol}: {e}")

            return df

        except Exception as e:
            self.logger.warning(f"Error fetching bars for {symbol}: {e}")
            return pd.DataFrame()

    def get_quote(self, symbol: str) -> dict:
        if not self._connected:
            self.connect()

        if not YF_AVAILABLE:
            self.logger.error("yfinance not installed")
            return {"symbol": symbol, "price": 0.0, "bid": 0.0, "ask": 0.0}

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info

            price = getattr(info, "last_price", 0.0) or 0.0
            bid = getattr(info, "bid", 0.0) or 0.0
            ask = getattr(info, "ask", 0.0) or 0.0

            return {"symbol": symbol, "price": float(price), "bid": float(bid), "ask": float(ask)}
        except Exception as e:
            self.logger.warning(f"Error fetching quote for {symbol}: {e}")
            return {"symbol": symbol, "price": 0.0, "bid": 0.0, "ask": 0.0}
