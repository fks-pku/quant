"""Alpha Vantage data provider adapter."""

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
import pandas as pd
import requests

from quant.domain.ports.data_feed import DataFeed
from quant.shared.utils.logger import setup_logger


class AlphaVantageProvider(DataFeed):
    """Alpha Vantage API adapter for historical and real-time data."""

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: Optional[str] = None):
        self._connected = False
        self.api_key = api_key or ""
        self.logger = setup_logger("AlphaVantageProvider")
        self._callbacks: List[Callable] = []
        self._rate_limit_calls = 0
        self._rate_limit_reset = datetime.now()

    @property
    def name(self) -> str:
        return "alpha_vantage"

    def connect(self) -> None:
        """Connect to Alpha Vantage (validates API key)."""
        if not self.api_key:
            self.logger.warning("No Alpha Vantage API key provided")
        self._connected = True
        self.logger.info("Connected to Alpha Vantage")

    def disconnect(self) -> None:
        """Disconnect from Alpha Vantage."""
        self._connected = False

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def _check_rate_limit(self) -> None:
        """Check and enforce rate limit (25 requests/day on free tier)."""
        now = datetime.now()
        if (now - self._rate_limit_reset).days >= 1:
            self._rate_limit_calls = 0
            self._rate_limit_reset = now

        if self._rate_limit_calls >= 25:
            raise Exception("Alpha Vantage rate limit exceeded (25 req/day on free tier)")

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "5m",
    ) -> pd.DataFrame:
        """
        Get historical bars from Alpha Vantage.

        timeframe: 1m, 5m, 15m, 30m, 60m (intraday) or daily, weekly, monthly
        """
        self._check_rate_limit()

        func = "TIME_SERIES_INTRADAY" if timeframe.endswith("m") else "TIME_SERIES_DAILY"
        interval = timeframe if timeframe.endswith("m") else None

        params: Dict[str, Any] = {
            "function": func,
            "symbol": symbol,
            "apikey": self.api_key,
            "outputsize": "full",
        }
        if interval:
            params["interval"] = interval

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            data = response.json()

            key_prefix = "Time Series (Intraday)" if interval else "Time Series (Daily)"
            if key_prefix not in data:
                self.logger.error(f"No data returned for {symbol}")
                return pd.DataFrame()

            records = []
            for timestamp_str, values in data[key_prefix].items():
                if interval:
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                else:
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d")

                if start <= timestamp <= end:
                    records.append({
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "open": float(values["1. open"]),
                        "high": float(values["2. high"]),
                        "low": float(values["3. low"]),
                        "close": float(values["4. close"]),
                        "volume": int(values["5. volume"]),
                    })

            self._rate_limit_calls += 1
            return pd.DataFrame(records)

        except Exception as e:
            self.logger.error(f"Error fetching bars for {symbol}: {e}")
            return pd.DataFrame()

    def get_quote(self, symbol: str) -> dict:
        """Get current quote from Alpha Vantage."""
        self._check_rate_limit()

        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
            "apikey": self.api_key,
        }

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            data = response.json()

            if "Global Quote" not in data or not data["Global Quote"]:
                return {
                    "timestamp": datetime.now(),
                    "symbol": symbol,
                    "bid": 0,
                    "ask": 0,
                    "bid_size": 0,
                    "ask_size": 0,
                }

            q = data["Global Quote"]
            self._rate_limit_calls += 1

            return {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "bid": float(q.get("03. high", 0)),
                "ask": float(q.get("04. low", 0)),
                "bid_size": 0,
                "ask_size": 0,
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
        """Alpha Vantage does not support real-time streaming."""
        self.logger.warning("Alpha Vantage does not support real-time subscriptions")
        self._callbacks.append(callback)

    def unsubscribe(self, symbols: List[str]) -> None:
        self.logger.warning("Alpha Vantage does not support real-time unsubscriptions")
