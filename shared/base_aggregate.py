from dataclasses import dataclass, field
from typing import Any


@dataclass
class DomainEvent:
    """Marker base class for all domain events."""
    pass


class AggregateRoot:
    """
    Base class for all aggregates.
    Collects domain events raised during a command.
    The application layer collects and dispatches them after persistence.
    """

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []

    def _raise_event(self, event: DomainEvent) -> None:
        self._events.append(event)

    def collect_events(self) -> list[DomainEvent]:
        """
        Called by the application layer after the aggregate is persisted.
        Clears the internal list so events are only dispatched once.
        """
        events = list(self._events)
        self._events.clear()
        return events
