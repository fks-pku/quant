from abc import ABC, abstractmethod
from typing import Callable

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

    def publish_nowait(self, event: Event) -> None:
        self.publish(event)

    @abstractmethod
    def clear(self) -> None:
        pass
