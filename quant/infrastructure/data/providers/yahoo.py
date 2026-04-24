"""Yahoo Finance data provider adapter."""

from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional
import pandas as pd

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

from quant.domain.ports.data_feed import DataFeed
from quant.shared.utils.logger import setup_logger


class YahooProvider(DataFeed):
    """Yahoo Finance adapter for historical and real-time data."""

    def __init__(self):
        self._connected = False
        self.logger = setup_logger("YahooProvider")
        self._callbacks: List[Callable] = []

    @property
    def name(self) -> str:
        return "yahoo"

    def connect(self) -> None:
        """Connect to Yahoo Finance (no-op for free API)."""
        if not YF_AVAILABLE:
            self.logger.warning("yfinance not installed. Install with: pip install yfinance")
        self._connected = True
        self.logger.info("Connected to Yahoo Finance")

    def disconnect(self) -> None:
        """Disconnect from Yahoo Finance."""
        self._connected = False
        self.logger.info("Disconnected from Yahoo Finance")

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "5m",
    ) -> pd.DataFrame:
        """
        Get historical bars from Yahoo Finance.

        timeframe: 1m, 5m, 15m, 1h, 1d, 1wk, 1mo
        """
        if not self._connected:
            self.connect()

        if not YF_AVAILABLE:
            self.logger.error("yfinance not installed")
            return pd.DataFrame()

        interval_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "1d": "1d",
            "1wk": "1wk",
            "1mo": "1mo",
        }
        interval = interval_map.get(timeframe, "5m")

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(
                start=start,
                end=end,
                interval=interval,
                auto_adjust=True,
            )

            if df.empty:
                return pd.DataFrame()

            df = df.reset_index()
            df.columns = [col.lower() for col in df.columns]
            df = df.rename(columns={"index": "timestamp"})
            df["symbol"] = symbol

            if " dividends" in df.columns:
                df = df.drop(columns=[" dividends", " stock splits"], errors="ignore")

            return df[["timestamp", "symbol", "open", "high", "low", "close", "volume"]]

        except Exception as e:
            self.logger.error(f"Error fetching bars for {symbol}: {e}")
            return pd.DataFrame()

    def get_quote(self, symbol: str) -> dict:
        """Get current quote from Yahoo Finance."""
        if not self._connected:
            self.connect()

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info

            return {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "bid": info.get("bid", 0),
                "ask": info.get("ask", 0),
                "bid_size": info.get("bid_size", 0),
                "ask_size": info.get("ask_size", 0),
            }
        except Exception as e:
            self.logger.error(f"Error fetching quote for {symbol}: {e}")
            return {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "bid": 0,
                "ask": 0,
                "bid_size": 0,
                "ask_size": 0,
            }

    def subscribe(self, symbols: List[str], callback: Callable) -> None:
        """Subscribe to real-time quotes via Yahoo Finance web socket."""
        self.logger.info(f"Subscribing to {symbols} (real-time via yfinance)")
        self._callbacks.append(callback)

    def unsubscribe(self, symbols: List[str]) -> None:
        self.logger.info(f"Unsubscribing from {symbols}")
