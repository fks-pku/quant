from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Optional

from quant.domain.events.base import Event, EventType

EventSubscriber = Callable[[Event], None]


class EventPublisher(ABC):

    @abstractmethod
    def subscribe(self, event_type: EventType, handler: EventSubscriber) -> None:
        pass

    @abstractmethod
    def unsubscribe(self, event_type: EventType, handler: EventSubscriber) -> None:
        pass

    @abstractmethod
    def publish(self, event: Event) -> None:
        pass

    def publish_nowait(self, event_type: EventType, data: Any = None, source: Optional[str] = None) -> None:
        event = Event(
            event_type=event_type,
            timestamp=datetime.now(),
            data=data,
            source=source,
        )
        self.publish(event)

    @abstractmethod
    def clear(self) -> None:
        pass
