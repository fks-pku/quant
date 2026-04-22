"""Infrastructure event bus implementing the domain EventPublisher port."""

from datetime import datetime
from typing import Callable, Dict, List, Any, Optional
import threading

from quant.domain.events.base import EventType, Event
from quant.domain.ports.event_publisher import EventPublisher
from quant.shared.utils.logger import setup_logger

_logger = setup_logger("EventBus")


class EventBus(EventPublisher):

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event_type: EventType, callback: Callable) -> None:
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable) -> None:
        with self._lock:
            if event_type in self._subscribers:
                self._subscribers[event_type].remove(callback)

    def publish(self, event: Event) -> None:
        with self._lock:
            callbacks = self._subscribers.get(event.event_type, []).copy()

        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                _logger.error(f"Error in event callback: {e}")

    def publish_nowait(self, event_type: EventType, data: Any = None, source: Optional[str] = None) -> None:
        event = Event(
            event_type=event_type,
            timestamp=datetime.now(),
            data=data,
            source=source,
        )
        self.publish(event)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()
