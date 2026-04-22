from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from quant.domain.events.base import Event, EventType
from quant.domain.models.bar import Bar


@dataclass(frozen=True)
class BarEvent(Event):
    bar: Bar = None

    def __init__(self, bar: Bar, source: Optional[str] = None,
                 timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.BAR, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'bar', bar)

    @property
    def symbol(self) -> str:
        return self.bar.symbol if self.bar else ""

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.bar:
            d["symbol"] = self.bar.symbol
            d["open"] = self.bar.open
            d["high"] = self.bar.high
            d["low"] = self.bar.low
            d["close"] = self.bar.close
            d["volume"] = self.bar.volume
            d["timeframe"] = self.bar.timeframe
        return d


@dataclass(frozen=True)
class QuoteEvent(Event):
    symbol: str = ""
    bid: float = 0.0
    ask: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    quote_timestamp: Optional[datetime] = None

    def __init__(self, symbol: str, bid: float, ask: float,
                 bid_size: float = 0.0, ask_size: float = 0.0,
                 quote_timestamp: Optional[datetime] = None,
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.QUOTE, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'symbol', symbol)
        object.__setattr__(self, 'bid', bid)
        object.__setattr__(self, 'ask', ask)
        object.__setattr__(self, 'bid_size', bid_size)
        object.__setattr__(self, 'ask_size', ask_size)
        object.__setattr__(self, 'quote_timestamp', quote_timestamp)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["symbol"] = self.symbol
        d["bid"] = self.bid
        d["ask"] = self.ask
        d["bid_size"] = self.bid_size
        d["ask_size"] = self.ask_size
        d["mid"] = self.mid
        d["spread"] = self.spread
        return d


@dataclass(frozen=True)
class MarketOpenEvent(Event):
    market: str = ""
    open_time: Optional[datetime] = None

    def __init__(self, market: str, open_time: Optional[datetime] = None,
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.MARKET_OPEN, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'market', market)
        object.__setattr__(self, 'open_time', open_time)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["market"] = self.market
        d["open_time"] = self.open_time.isoformat() if self.open_time else None
        return d


@dataclass(frozen=True)
class MarketCloseEvent(Event):
    market: str = ""
    close_time: Optional[datetime] = None

    def __init__(self, market: str, close_time: Optional[datetime] = None,
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.MARKET_CLOSE, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'market', market)
        object.__setattr__(self, 'close_time', close_time)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["market"] = self.market
        d["close_time"] = self.close_time.isoformat() if self.close_time else None
        return d
