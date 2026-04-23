from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Optional
import uuid


class EventType(Enum):
    ORDER_SUBMITTED = auto()
    ORDER_FILLED = auto()
    ORDER_CANCELLED = auto()
    ORDER_REJECTED = auto()
    TRADE_OPENED = auto()
    TRADE_CLOSED = auto()
    TRADE = auto()
    POSITION_UPDATE = auto()
    BAR = auto()
    QUOTE = auto()
    MARKET_OPEN = auto()
    MARKET_CLOSE = auto()
    STRATEGY_SIGNAL = auto()
    SYSTEM_START = auto()
    SYSTEM_STOP = auto()
    SYSTEM_SHUTDOWN = auto()
    RISK_CHECK = auto()
    FILL_PROCESSED = auto()
    ORDER_RECORDED = auto()
    RESEARCH_SEARCH_DONE = "research_search_done"
    RESEARCH_IDEA_SCORED = "research_idea_scored"
    RESEARCH_CODE_READY = "research_code_ready"
    RESEARCH_REPORT_DONE = "research_report_done"
    RESEARCH_ERROR = "research_error"


@dataclass(frozen=True)
class Event:
    event_type: EventType
    timestamp: datetime = field(default_factory=datetime.now)
    data: Any = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.name,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "data": self.data,
        }
