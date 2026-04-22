from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from quant.domain.events.base import Event, EventType


@dataclass(frozen=True)
class StrategySignalEvent(Event):
    strategy_name: str = ""
    symbol: str = ""
    signal: str = ""
    confidence: float = 0.0
    metadata: Optional[Dict[str, Any]] = None

    def __init__(self, strategy_name: str, symbol: str, signal: str,
                 confidence: float = 0.0, metadata: Optional[Dict[str, Any]] = None,
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.STRATEGY_SIGNAL, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'strategy_name', strategy_name)
        object.__setattr__(self, 'symbol', symbol)
        object.__setattr__(self, 'signal', signal)
        object.__setattr__(self, 'confidence', confidence)
        object.__setattr__(self, 'metadata', metadata or {})

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["strategy_name"] = self.strategy_name
        d["symbol"] = self.symbol
        d["signal"] = self.signal
        d["confidence"] = self.confidence
        d["metadata"] = self.metadata
        return d


@dataclass(frozen=True)
class SystemStartEvent(Event):
    mode: str = ""
    start_time: Optional[datetime] = None
    config: Optional[Dict[str, Any]] = None

    def __init__(self, mode: str, start_time: Optional[datetime] = None,
                 config: Optional[Dict[str, Any]] = None,
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.SYSTEM_START, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'mode', mode)
        object.__setattr__(self, 'start_time', start_time)
        object.__setattr__(self, 'config', config or {})

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["mode"] = self.mode
        d["start_time"] = self.start_time.isoformat() if self.start_time else None
        d["config"] = self.config
        return d


@dataclass(frozen=True)
class SystemStopEvent(Event):
    reason: str = ""
    stop_time: Optional[datetime] = None

    def __init__(self, reason: str = "", stop_time: Optional[datetime] = None,
                 source: Optional[str] = None, timestamp: Optional[datetime] = None):
        super().__init__(event_type=EventType.SYSTEM_STOP, source=source, timestamp=timestamp or datetime.now())
        object.__setattr__(self, 'reason', reason)
        object.__setattr__(self, 'stop_time', stop_time)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["reason"] = self.reason
        d["stop_time"] = self.stop_time.isoformat() if self.stop_time else None
        return d
