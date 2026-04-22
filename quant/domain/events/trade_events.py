from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from quant.domain.events.base import Event, EventType
from quant.domain.models.trade import Trade
from quant.domain.models.position import Position


@dataclass(frozen=True)
class TradeOpenedEvent(Event):
    trade: Trade = None

    def __init__(self, trade: Trade, source: Optional[str] = None,
                 timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.TRADE_OPENED, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'trade', trade)

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.trade:
            d["symbol"] = self.trade.symbol
            d["quantity"] = self.trade.quantity
            d["entry_price"] = self.trade.entry_price
            d["side"] = self.trade.side
        return d


@dataclass(frozen=True)
class TradeClosedEvent(Event):
    trade: Trade = None

    def __init__(self, trade: Trade, source: Optional[str] = None,
                 timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.TRADE_CLOSED, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'trade', trade)

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.trade:
            d["symbol"] = self.trade.symbol
            d["quantity"] = self.trade.quantity
            d["pnl"] = self.trade.pnl
            d["realized_pnl"] = self.trade.realized_pnl
        return d


@dataclass(frozen=True)
class PositionUpdateEvent(Event):
    position: Position = None
    symbol: str = ""

    def __init__(self, position: Position, symbol: Optional[str] = None,
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.POSITION_UPDATE, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'position', position)
        object.__setattr__(self, 'symbol', symbol or (position.symbol if position else ""))

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["symbol"] = self.symbol
        if self.position:
            d["quantity"] = self.position.quantity
            d["avg_cost"] = self.position.avg_cost
            d["unrealized_pnl"] = self.position.unrealized_pnl
        return d
