"""Data normalizer for unified schemas across providers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Union
import pandas as pd


@dataclass
class Bar:
    """Standardized bar data."""
    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class Quote:
    """Standardized quote data."""
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    bid_size: int
    ask_size: int


@dataclass
class MarketTrade:
    """Standardized market trade data (tick-level)."""
    timestamp: datetime
    symbol: str
    price: float
    size: float
    exchange: str


class Normalizer:
    """Normalizes data from different providers to standard schemas."""

    @staticmethod
    def normalize_bar(data: Union[Dict, pd.Series], symbol: Optional[str] = None) -> Bar:
        """
        Normalize bar data from any provider format.

        Expected input dict keys vary by provider, but common ones are:
        - timestamp/dt/index: datetime
        - symbol/s: symbol string
        - open/o: open price
        - high/h: high price
        - low/l: low price
        - close/c: close price
        - volume/v: volume
        """
        if isinstance(data, pd.Series):
            data = data.to_dict()

        ts = data.get("timestamp") or data.get("date") or data.get("datetime") or data.get("index")
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
        elif not isinstance(ts, datetime):
            ts = datetime.now()

        sym = data.get("symbol") or symbol or ""

        return Bar(
            timestamp=ts,
            symbol=sym,
            open=float(data.get("open") or data.get("o") or 0),
            high=float(data.get("high") or data.get("h") or 0),
            low=float(data.get("low") or data.get("l") or 0),
            close=float(data.get("close") or data.get("c") or 0),
            volume=int(data.get("volume") or data.get("v") or 0),
        )

    @staticmethod
    def normalize_quote(data: Dict, symbol: Optional[str] = None) -> Quote:
        """
        Normalize quote data from any provider format.
        """
        ts = data.get("timestamp") or datetime.now()
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
        elif not isinstance(ts, datetime):
            ts = datetime.now()

        return Quote(
            timestamp=ts,
            symbol=data.get("symbol") or symbol or "",
            bid=float(data.get("bid", 0)),
            ask=float(data.get("ask", 0)),
            bid_size=int(data.get("bid_size", 0)),
            ask_size=int(data.get("ask_size", 0)),
        )

    @staticmethod
    def normalize_trade(data: Dict, symbol: Optional[str] = None) -> MarketTrade:
        """
        Normalize trade data from any provider format.
        """
        ts = data.get("timestamp") or datetime.now()
        if isinstance(ts, str):
            ts = pd.to_datetime(ts)
        elif not isinstance(ts, datetime):
            ts = datetime.now()

        return MarketTrade(
            timestamp=ts,
            symbol=data.get("symbol") or symbol or "",
            price=float(data.get("price", 0)),
            size=float(data.get("size") or data.get("volume", 0)),
            exchange=data.get("exchange", ""),
        )

    @staticmethod
    def normalize_dataframe(df: pd.DataFrame, data_type: str = "bar") -> pd.DataFrame:
        """
        Normalize a DataFrame to standard column names.

        data_type: 'bar', 'quote', or 'trade'
        """
        df = df.copy()

        bar_mapping = {
            "timestamp": ["timestamp", "datetime", "index", "date", "time"],
            "symbol": ["symbol", "s", "ticker"],
            "open": ["open", "o", "open_price"],
            "high": ["high", "h", "high_price"],
            "low": ["low", "l", "low_price"],
            "close": ["close", "c", "close_price"],
            "volume": ["volume", "v", "vol"],
        }

        quote_mapping = {
            "timestamp": ["timestamp", "datetime", "index", "date"],
            "symbol": ["symbol", "s", "ticker"],
            "bid": ["bid", "b"],
            "ask": ["ask", "a"],
            "bid_size": ["bid_size", "bsize", "bidvol"],
            "ask_size": ["ask_size", "asize", "askvol"],
        }

        trade_mapping = {
            "timestamp": ["timestamp", "datetime", "index", "date"],
            "symbol": ["symbol", "s", "ticker"],
            "price": ["price", "p"],
            "size": ["size", "volume", "vol"],
            "exchange": ["exchange", "e", "market"],
        }

        mapping = {
            "bar": bar_mapping,
            "quote": quote_mapping,
            "trade": trade_mapping,
        }

        selected_mapping = mapping.get(data_type, bar_mapping)

        for standard_name, possible_names in selected_mapping.items():
            for col in possible_names:
                if col in df.columns:
                    df = df.rename(columns={col: standard_name})
                    break

        return df

    @staticmethod
    def to_standard_dict(bar: Bar) -> Dict[str, Any]:
        """Convert a Bar dataclass to a standard dictionary."""
        return {
            "timestamp": bar.timestamp,
            "symbol": bar.symbol,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
