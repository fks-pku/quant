"""Futu OpenAPI data provider adapter for Hong Kong equities."""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import pandas as pd

from quant.data.providers.base import DataProvider
from quant.utils.logger import setup_logger


class FutuProvider(DataProvider):
    """Futu OpenAPI adapter for real-time HK equity data."""

    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        super().__init__("futu")
        self.host = host
        self.port = port
        self.logger = setup_logger("FutuProvider")
        self._trd_api: Optional[Any] = None
        self._quote_api: Optional[Any] = None
        self._callbacks: Dict[str, List[Callable]] = {}

    def connect(self) -> None:
        """Connect to Futu OpenAPI."""
        try:
            from futu import OpenQuoteContext, OpenTTradeContext

            self._quote_api = OpenQuoteContext(host=self.host, port=self.port)
            self._connected = True
            self.logger.info(f"Connected to Futu OpenAPI at {self.host}:{self.port}")
        except ImportError:
            self.logger.error("futu-api not installed. Install with: pip install futu-api")
            raise
        except Exception as e:
            self.logger.error(f"Failed to connect to Futu: {e}")
            raise

    def disconnect(self) -> None:
        """Disconnect from Futu OpenAPI."""
        if self._quote_api:
            self._quote_api.close()
        if self._trd_api:
            self._trd_api.close()
        self._connected = False
        self.logger.info("Disconnected from Futu OpenAPI")

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
        Get historical bars from Futu.

        timeframe: 1m, 5m, 15m, 30m, 60m, 1d, 1w, 1m (monthly)
        Market prefix: HK.StockCode for HK stocks, US.Code for US stocks
        """
        if not self._connected or not self._quote_api:
            self.connect()

        subtype_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "60m": "60m",
            "1d": "1d",
            "1w": "1W",
            "1mo": "1MON",
        }
        subtype = subtype_map.get(timeframe, "5m")

        try:
            ret, data = self._quote_api.get_history_kline(
                code=symbol,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                subtype=subtype,
            )

            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return pd.DataFrame()

            df = pd.DataFrame(data)
            df["timestamp"] = pd.to_datetime(df["time_key"])
            df["symbol"] = symbol
            df = df.rename(columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            })

            return df[["timestamp", "symbol", "open", "high", "low", "close", "volume"]]

        except Exception as e:
            self.logger.error(f"Error fetching bars for {symbol}: {e}")
            return pd.DataFrame()

    def get_quote(self, symbol: str) -> dict:
        """Get current quote from Futu."""
        if not self._connected or not self._quote_api:
            self.connect()

        try:
            ret, data = self._quote_api.get_stock_quote([symbol])
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return {
                    "timestamp": datetime.now(),
                    "symbol": symbol,
                    "bid": 0,
                    "ask": 0,
                    "bid_size": 0,
                    "ask_size": 0,
                }

            quote = data.iloc[0]
            return {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "bid": float(quote.get("BidPrice", 0)),
                "ask": float(quote.get("AskPrice", 0)),
                "bid_size": int(quote.get("BidVol", 0)),
                "ask_size": int(quote.get("AskVol", 0)),
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
        """Subscribe to real-time quotes from Futu."""
        if not self._connected or not self._quote_api:
            self.connect()

        for symbol in symbols:
            if symbol not in self._callbacks:
                self._callbacks[symbol] = []
            self._callbacks[symbol].append(callback)

        ret, data = self._quote_api.subscribe_stock_quote(symbols)
        if ret != 0:
            self.logger.error(f"Failed to subscribe to {symbols}: {data}")
        else:
            self.logger.info(f"Subscribed to {symbols}")

    def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from real-time data."""
        if self._quote_api:
            self._quote_api.unsubscribe_stock_quote(symbols)
            for symbol in symbols:
                self._callbacks.pop(symbol, None)
