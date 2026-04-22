"""Akshare data provider for China A-share daily bars."""

import time
from datetime import datetime
from typing import List, Optional

import pandas as pd

from quant.infrastructure.data.providers.base import DataProvider
from quant.shared.utils.logger import setup_logger

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    ak = None  # type: ignore
    AKSHARE_AVAILABLE = False


class AkshareProvider(DataProvider):
    """Data provider for China A-share via akshare."""

    def __init__(self, min_interval: float = 0.5):
        super().__init__("akshare")
        self._min_interval = min_interval
        self._last_request_time = 0.0
        self.logger = setup_logger("AkshareProvider")

    def connect(self) -> None:
        if not AKSHARE_AVAILABLE:
            self.logger.warning("akshare not installed")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    @staticmethod
    def _to_akshare_date(dt: datetime) -> str:
        return dt.strftime("%Y%m%d")

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        if not self._connected:
            self.connect()

        if not AKSHARE_AVAILABLE:
            self.logger.error("akshare not installed")
            return pd.DataFrame()

        if timeframe not in ("1d", "day", "daily"):
            self.logger.warning(f"akshare A-share only supports daily bars, got {timeframe}")
            return pd.DataFrame()

        self._rate_limit()

        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=self._to_akshare_date(start),
                end_date=self._to_akshare_date(end),
                adjust="qfq",
            )
        except Exception as e:
            self.logger.warning(f"Error fetching bars for {symbol}: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        df = self._normalize_bars(df, symbol)
        return df

    def _normalize_bars(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()

        col_mapping = {
            "日期": "timestamp",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        }

        df = df.rename(columns={k: v for k, v in col_mapping.items() if k in df.columns})

        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        df["symbol"] = symbol

        desired = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
        available = [c for c in desired if c in df.columns]
        df = df[available]

        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.dropna(subset=[c for c in ["open", "high", "low", "close"] if c in df.columns])

    def get_quote(self, symbol: str) -> dict:
        if not self._connected:
            self.connect()

        if not AKSHARE_AVAILABLE:
            self.logger.error("akshare not installed")
            return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}

        self._rate_limit()

        try:
            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == symbol]
            if row.empty:
                return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}

            price = float(row["最新价"].iloc[0]) if "最新价" in row.columns else 0.0
            return {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "bid": price,
                "ask": price,
                "bid_size": 0,
                "ask_size": 0,
            }
        except Exception as e:
            self.logger.warning(f"Error fetching quote for {symbol}: {e}")
            return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}
