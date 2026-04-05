from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(str, Enum):
    OPEN = "open"
    ESCALATED = "escalated"
    CLOSED = "closed"


class MessageAuthor(str, Enum):
    CUSTOMER = "customer"
    AGENT = "agent"
    SYSTEM = "system"


# SLA response windows per priority.
# These are the canonical values — the only place in the codebase
# that maps a Priority to a deadline duration.
SLA_WINDOWS: dict[Priority, timedelta] = {
    Priority.LOW: timedelta(hours=24),
    Priority.MEDIUM: timedelta(hours=8),
    Priority.HIGH: timedelta(hours=4),
    Priority.URGENT: timedelta(hours=1),
}

# How long a customer has to reply before the ticket is auto-closed.
CUSTOMER_INACTIVITY_WINDOW = timedelta(days=7)

# How long after closure a customer may reopen.
REOPEN_WINDOW = timedelta(days=7)

# Escalation reduces the remaining SLA by this fraction.
ESCALATION_REDUCTION = 0.33

# If agent doesn't open an escalated ticket within this fraction
# of the reduced window, it is auto-reassigned.
AUTO_REASSIGN_THRESHOLD = 0.50


@dataclass(frozen=True)
class SLAPolicy:
    """
    Calculates deadlines from a Priority.
    Frozen dataclass — immutable, safe to pass around.
    """
    priority: Priority

    @property
    def response_window(self) -> timedelta:
        return SLA_WINDOWS[self.priority]

    def reduced_window(self, original_window: timedelta) -> timedelta:
        """
        Returns the escalation-reduced window.
        Reduction is applied to whatever window remains, not the original SLA.
        """
        reduction = original_window * ESCALATION_REDUCTION
        return original_window - reduction

    def auto_reassign_window(self, reduced_window: timedelta) -> timedelta:
        """
        Returns the window within which an agent must open an escalated
        ticket before it is auto-reassigned.
        """
        return reduced_window * AUTO_REASSIGN_THRESHOLD


@dataclass(frozen=True)
class Message:
    """
    A single message appended to a ticket thread.
    Immutable — messages are never edited.
    """
    author: MessageAuthor
    author_id: str
    body: str
