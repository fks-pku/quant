"""Internal event bus for pub/sub communication between components."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Any, Optional
import threading


class EventType(Enum):
    """Event types for the internal event bus."""

    BAR = "bar"
    QUOTE = "quote"
    TRADE = "trade"
    ORDER_SUBMIT = "order_submit"
    ORDER_FILL = "order_fill"
    ORDER_CANCEL = "order_cancel"
    ORDER_REJECT = "order_reject"
    POSITION_UPDATE = "position_update"
    RISK_CHECK = "risk_check"
    MARKET_OPEN = "market_open"
    MARKET_CLOSE = "market_close"
    STRATEGY_SIGNAL = "strategy_signal"
    SYSTEM_SHUTDOWN = "system_shutdown"


@dataclass
class Event:
    """Base event class."""

    event_type: EventType
    timestamp: datetime
    data: Any
    source: Optional[str] = None


class EventBus:
    """Publish/subscribe event bus for component communication."""

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_type: EventType, callback: Callable) -> None:
        """Subscribe to an event type."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable) -> None:
        """Unsubscribe from an event type."""
        with self._lock:
            if event_type in self._subscribers:
                self._subscribers[event_type].remove(callback)

    def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        with self._lock:
            callbacks = self._subscribers.get(event.event_type, []).copy()

        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                print(f"Error in event callback: {e}")

    def publish_nowait(self, event_type: EventType, data: Any, source: Optional[str] = None) -> None:
        """Publish an event without waiting (fire and forget)."""
        event = Event(
            event_type=event_type,
            timestamp=datetime.now(),
            data=data,
            source=source,
        )
        self.publish(event)

    def clear(self) -> None:
        """Clear all subscribers."""
        with self._lock:
            self._subscribers.clear()
