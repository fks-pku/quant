"""Futu OpenAPI data provider adapter for HK and US equities."""

from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
import pandas as pd

from quant.data.providers.base import DataProvider
from quant.utils.logger import setup_logger


class MarketStatus(Enum):
    """Market status enum."""
    OPEN = "open"
    CLOSED = "closed"
    PRE_MARKET = "pre_market"
    AFTER_HOURS = "after_hours"
    UNKNOWN = "unknown"


class FutuProvider(DataProvider):
    """Futu OpenAPI adapter for HK and US equity data."""

    SUBTYPE_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "60m": "60m",
        "1h": "60m",
        "1d": "1d",
        "1w": "1W",
        "1mo": "1MON",
    }

    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        super().__init__("futu")
        self.host = host
        self.port = port
        self.logger = setup_logger("FutuProvider")
        self._quote_api: Optional[Any] = None
        self._trd_api: Optional[Any] = None
        self._callbacks: Dict[str, Dict[str, List[Callable]]] = {
            "quote": {},
            "kline": {},
            "orderbook": {},
            "trade": {},
        }

    def connect(self) -> None:
        """Connect to Futu OpenAPI."""
        try:
            from futu import OpenQuoteContext

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
            self._quote_api = None
        if self._trd_api:
            self._trd_api.close()
            self._trd_api = None
        self._connected = False
        self.logger.info("Disconnected from Futu OpenAPI")

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def _ensure_connected(self) -> None:
        """Ensure quote API is connected."""
        if not self._connected or not self._quote_api:
            self.connect()

    def _get_market_from_symbol(self, symbol: str) -> str:
        """Extract market from symbol prefix."""
        if symbol.startswith("HK."):
            return "HK"
        elif symbol.startswith("US."):
            return "US"
        return "HK"

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
        self._ensure_connected()

        subtype = self.SUBTYPE_MAP.get(timeframe, "5m")

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

            if data is None or data.empty:
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
        self._ensure_connected()

        try:
            ret, data = self._quote_api.get_stock_quote([symbol])
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return self._empty_quote(symbol)

            if data is None or data.empty:
                return self._empty_quote(symbol)

            quote = data.iloc[0]
            return {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "bid": float(quote.get("BidPrice", 0)),
                "ask": float(quote.get("AskPrice", 0)),
                "bid_size": int(quote.get("BidVol", 0)),
                "ask_size": int(quote.get("AskVol", 0)),
                "last": float(quote.get("LastPrice", 0)),
                "open": float(quote.get("Open", 0)),
                "high": float(quote.get("High", 0)),
                "low": float(quote.get("Low", 0)),
                "volume": int(quote.get("Volume", 0)),
                "change": float(quote.get("ChangeVal", 0)),
                "change_pct": float(quote.get("ChangeRate", 0)),
            }

        except Exception as e:
            self.logger.error(f"Error fetching quote for {symbol}: {e}")
            return self._empty_quote(symbol)

    def _empty_quote(self, symbol: str) -> dict:
        """Return an empty quote dict."""
        return {
            "timestamp": datetime.now(),
            "symbol": symbol,
            "bid": 0.0,
            "ask": 0.0,
            "bid_size": 0,
            "ask_size": 0,
            "last": 0.0,
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "volume": 0,
            "change": 0.0,
            "change_pct": 0.0,
        }

    def get_orderbook(self, symbol: str, depth: int = 10) -> dict:
        """
        Get order book (market depth) for a symbol.

        Args:
            symbol: Stock symbol (e.g., HK.00700, US.AAPL)
            depth: Number of price levels (default 10, max 50)

        Returns:
            dict with bid_prices, bid_sizes, ask_prices, ask_sizes, timestamp
        """
        self._ensure_connected()

        try:
            ret, data = self._quote_api.get_order_book(symbol, num=depth)
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return self._empty_orderbook(symbol)

            if data is None:
                return self._empty_orderbook(symbol)

            bids = data.get("Bid", [])
            asks = data.get("Ask", [])

            return {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "bid_prices": [float(b.get("Price", 0)) for b in bids],
                "bid_sizes": [int(b.get("Volume", 0)) for b in bids],
                "ask_prices": [float(a.get("Price", 0)) for a in asks],
                "ask_sizes": [int(a.get("Volume", 0)) for a in asks],
            }

        except Exception as e:
            self.logger.error(f"Error fetching orderbook for {symbol}: {e}")
            return self._empty_orderbook(symbol)

    def _empty_orderbook(self, symbol: str) -> dict:
        """Return an empty orderbook dict."""
        return {
            "timestamp": datetime.now(),
            "symbol": symbol,
            "bid_prices": [],
            "bid_sizes": [],
            "ask_prices": [],
            "ask_sizes": [],
        }

    def get_trades(self, symbol: str, num: int = 100) -> pd.DataFrame:
        """
        Get recent tick-by-tick trades.

        Args:
            symbol: Stock symbol (e.g., HK.00700, US.AAPL)
            num: Number of recent trades (default 100)

        Returns:
            DataFrame with [timestamp, symbol, price, volume, direction]
            direction: 1 = buy (uptick), -1 = sell (downtick)
        """
        self._ensure_connected()

        try:
            ret, data = self._quote_api.get_rt_ticker(symbol, num=num)
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return pd.DataFrame(columns=["timestamp", "symbol", "price", "volume", "direction"])

            if data is None or data.empty:
                return pd.DataFrame(columns=["timestamp", "symbol", "price", "volume", "direction"])

            df = pd.DataFrame(data)
            df["timestamp"] = pd.to_datetime(df["Time"], unit="s")
            df["symbol"] = symbol
            df["direction"] = df["Direction"].apply(lambda x: 1 if x == "BUY" else -1)
            df = df.rename(columns={
                "Price": "price",
                "Volume": "volume",
            })

            return df[["timestamp", "symbol", "price", "volume", "direction"]]

        except Exception as e:
            self.logger.error(f"Error fetching trades for {symbol}: {e}")
            return pd.DataFrame(columns=["timestamp", "symbol", "price", "volume", "direction"])

    def get_market_status(self, market: str = "HK") -> str:
        """
        Get market status (open/closed) for HK or US.

        Args:
            market: "HK" or "US"

        Returns:
            "open", "closed", "pre_market", "after_hours", or "unknown"
        """
        self._ensure_connected()

        try:
            from futu import TrdMarket

            market_map = {
                "HK": TrdMarket.HK,
                "US": TrdMarket.US,
            }
            trd_market = market_map.get(market, TrdMarket.HK)

            ret, data = self._quote_api.get_market_state([market])
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return MarketStatus.UNKNOWN.value

            if data is None or data.empty:
                return MarketStatus.UNKNOWN.value

            state = data.iloc[0].get("State", 0)
            state_map = {
                0: MarketStatus.UNKNOWN.value,
                1: MarketStatus.CLOSED.value,
                2: MarketStatus.OPEN.value,
                3: MarketStatus.PRE_MARKET.value,
                4: MarketStatus.AFTER_HOURS.value,
            }
            return state_map.get(state, MarketStatus.UNKNOWN.value)

        except Exception as e:
            self.logger.error(f"Error fetching market status for {market}: {e}")
            return MarketStatus.UNKNOWN.value

    def get_static_info(self, symbols: List[str]) -> dict:
        """
        Get static info (name, lot size, market, type) for symbols.

        Args:
            symbols: List of stock symbols (e.g., ["HK.00700", "US.AAPL"])

        Returns:
            dict: {symbol: {name, lot_size, security_type, market, listing_date, ...}}
        """
        self._ensure_connected()

        try:
            ret, data = self._quote_api.get_stock_basicinfo(code_list=symbols)
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return {}

            if data is None or data.empty:
                return {}

            result = {}
            for _, row in data.iterrows():
                symbol = row.get("code", "")
                result[symbol] = {
                    "name": row.get("name", ""),
                    "lot_size": int(row.get("lot_size", 0)),
                    "security_type": row.get("security_type", ""),
                    "market": row.get("market", ""),
                    "listing_date": row.get("listing_date", ""),
                    "stock_id": row.get("stock_id", ""),
                }

            return result

        except Exception as e:
            self.logger.error(f"Error fetching static info: {e}")
            return {}

    def get_capital_distribution(self, symbol: str) -> dict:
        """
        Get capital distribution (large/medium/small order flow).

        Args:
            symbol: Stock symbol (e.g., HK.00700, US.AAPL)

        Returns:
            dict with large_net, medium_net, small_net, timestamp
        """
        self._ensure_connected()

        try:
            ret, data = self._quote_api.get_capital_distribution([symbol])
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return self._empty_capital(symbol)

            if data is None or data.empty:
                return self._empty_capital(symbol)

            row = data.iloc[0]
            return {
                "timestamp": datetime.now(),
                "symbol": symbol,
                "large_net": float(row.get("LargeNet", 0)),
                "medium_net": float(row.get("MediumNet", 0)),
                "small_net": float(row.get("SmallNet", 0)),
                "large_inflow": float(row.get("LargeInflow", 0)),
                "medium_inflow": float(row.get("MediumInflow", 0)),
                "small_inflow": float(row.get("SmallInflow", 0)),
            }

        except Exception as e:
            self.logger.error(f"Error fetching capital distribution for {symbol}: {e}")
            return self._empty_capital(symbol)

    def _empty_capital(self, symbol: str) -> dict:
        """Return empty capital distribution dict."""
        return {
            "timestamp": datetime.now(),
            "symbol": symbol,
            "large_net": 0.0,
            "medium_net": 0.0,
            "small_net": 0.0,
            "large_inflow": 0.0,
            "medium_inflow": 0.0,
            "small_inflow": 0.0,
        }

    def get_market_snapshot(self, symbols: List[str]) -> pd.DataFrame:
        """
        Get market snapshot for multiple symbols.

        Args:
            symbols: List of stock symbols

        Returns:
            DataFrame with snapshot data
        """
        self._ensure_connected()

        try:
            ret, data = self._quote_api.get_market_snapshot(symbols)
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return pd.DataFrame()

            if data is None or data.empty:
                return pd.DataFrame()

            return data

        except Exception as e:
            self.logger.error(f"Error fetching market snapshot: {e}")
            return pd.DataFrame()

    def subscribe(self, symbols: List[str], callback: Callable) -> None:
        """Subscribe to real-time quotes from Futu (backward compat)."""
        self.subscribe_quotes(symbols, callback)

    def subscribe_quotes(self, symbols: List[str], callback: Callable) -> None:
        """
        Subscribe to real-time quotes.

        Args:
            symbols: List of stock symbols
            callback: Function(symbol, quote_dict) to call on updates
        """
        self._ensure_connected()

        for symbol in symbols:
            if symbol not in self._callbacks["quote"]:
                self._callbacks["quote"][symbol] = []
            self._callbacks["quote"][symbol].append(callback)

        ret, data = self._quote_api.subscribe_stock_quote(symbols)
        if ret != 0:
            self.logger.error(f"Failed to subscribe to {symbols}: {data}")
        else:
            self.logger.info(f"Subscribed to quotes: {symbols}")

    def subscribe_kline(self, symbols: List[str], timeframe: str = "1m", callback: Optional[Callable] = None) -> None:
        """
        Subscribe to real-time K-line updates.

        Args:
            symbols: List of stock symbols
            timeframe: K-line timeframe (1m, 5m, 15m, 30m, 60m, 1d, 1w)
            callback: Optional function(symbol, kline_dict) to call on updates
        """
        self._ensure_connected()

        subtype = self.SUBTYPE_MAP.get(timeframe, "1m")

        for symbol in symbols:
            if symbol not in self._callbacks["kline"]:
                self._callbacks["kline"][symbol] = []
            if callback:
                self._callbacks["kline"][symbol].append(callback)

        ret, data = self._quote_api.subscribe_cur_kline(symbols, subtype=subtype)
        if ret != 0:
            self.logger.error(f"Failed to subscribe to K-line for {symbols}: {data}")
        else:
            self.logger.info(f"Subscribed to K-line {timeframe}: {symbols}")

    def subscribe_orderbook(self, symbols: List[str], depth: int = 10, callback: Optional[Callable] = None) -> None:
        """
        Subscribe to real-time order book updates.

        Args:
            symbols: List of stock symbols
            depth: Number of price levels
            callback: Optional function(symbol, orderbook_dict) to call on updates
        """
        self._ensure_connected()

        for symbol in symbols:
            if symbol not in self._callbacks["orderbook"]:
                self._callbacks["orderbook"][symbol] = []
            if callback:
                self._callbacks["orderbook"][symbol].append(callback)

        ret, data = self._quote_api.subscribe_order_book(symbols, num=depth)
        if ret != 0:
            self.logger.error(f"Failed to subscribe to orderbook for {symbols}: {data}")
        else:
            self.logger.info(f"Subscribed to orderbook depth={depth}: {symbols}")

    def subscribe_trades(self, symbols: List[str], callback: Optional[Callable] = None) -> None:
        """
        Subscribe to real-time tick-by-tick trades.

        Args:
            symbols: List of stock symbols
            callback: Optional function(symbol, trade_dict) to call on updates
        """
        self._ensure_connected()

        for symbol in symbols:
            if symbol not in self._callbacks["trade"]:
                self._callbacks["trade"][symbol] = []
            if callback:
                self._callbacks["trade"][symbol].append(callback)

        ret, data = self._quote_api.subscribe_rt_ticker(symbols)
        if ret != 0:
            self.logger.error(f"Failed to subscribe to trades for {symbols}: {data}")
        else:
            self.logger.info(f"Subscribed to trades: {symbols}")

    def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from real-time data (backward compat)."""
        self.unsubscribe_quotes(symbols)

    def unsubscribe_quotes(self, symbols: List[str]) -> None:
        """Unsubscribe from real-time quotes."""
        if self._quote_api:
            self._quote_api.unsubscribe_stock_quote(symbols)
            for symbol in symbols:
                self._callbacks["quote"].pop(symbol, None)
        self.logger.info(f"Unsubscribed from quotes: {symbols}")

    def unsubscribe_kline(self, symbols: List[str]) -> None:
        """Unsubscribe from K-line updates."""
        if self._quote_api:
            for symbol in symbols:
                self._quote_api.unsubscribe_cur_kline([symbol])
                self._callbacks["kline"].pop(symbol, None)
        self.logger.info(f"Unsubscribed from K-line: {symbols}")

    def unsubscribe_orderbook(self, symbols: List[str]) -> None:
        """Unsubscribe from orderbook updates."""
        if self._quote_api:
            for symbol in symbols:
                self._quote_api.unsubscribe_order_book([symbol])
                self._callbacks["orderbook"].pop(symbol, None)
        self.logger.info(f"Unsubscribed from orderbook: {symbols}")

    def unsubscribe_trades(self, symbols: List[str]) -> None:
        """Unsubscribe from trade updates."""
        if self._quote_api:
            for symbol in symbols:
                self._quote_api.unsubscribe_rt_ticker([symbol])
                self._callbacks["trade"].pop(symbol, None)
        self.logger.info(f"Unsubscribed from trades: {symbols}")

    def unsubscribe_all(self) -> None:
        """Unsubscribe from all subscriptions."""
        if self._quote_api:
            self._quote_api.unsubscribe_all()
        self._callbacks = {
            "quote": {},
            "kline": {},
            "orderbook": {},
            "trade": {},
        }
        self.logger.info("Unsubscribed from all Futu data")

    def get_subscription_info(self) -> dict:
        """
        Get subscription information.

        Returns:
            dict with subscribed symbols by type
        """
        if not self._quote_api:
            return {}

        try:
            ret, data = self._quote_api.query_subscription()
            if ret == 0:
                return data if data else {}
        except Exception as e:
            self.logger.error(f"Error getting subscription info: {e}")
        return {}

    def get_history_kl_quota(self) -> dict:
        """
        Get historical K-line quota usage.

        Returns:
            dict with {remain, used, total}
        """
        self._ensure_connected()

        try:
            ret, data = self._quote_api.get_history_kl_quota()
            if ret != 0:
                self.logger.error(f"Futu error: {data}")
                return {"remain": 0, "used": 0, "total": 0}
            return data if data else {"remain": 0, "used": 0, "total": 0}
        except Exception as e:
            self.logger.error(f"Error fetching K-line quota: {e}")
            return {"remain": 0, "used": 0, "total": 0}
