from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from shared.base_aggregate import DomainEvent
from ticket.domain.value_objects import MessageAuthor, Priority, TicketStatus


@dataclass
class TicketOpened(DomainEvent):
    ticket_id: UUID
    customer_id: str
    priority: Priority
    subject: str
    body: str
    sla_deadline: datetime
    opened_at: datetime


@dataclass
class MessageAppended(DomainEvent):
    ticket_id: UUID
    author: MessageAuthor
    author_id: str
    body: str
    appended_at: datetime


@dataclass
class TicketEscalated(DomainEvent):
    ticket_id: UUID
    escalated_by: str          # customer_id
    original_deadline: datetime
    new_deadline: datetime     # deadline after 33% reduction
    auto_reassign_deadline: datetime
    escalated_at: datetime


@dataclass
class TicketClosed(DomainEvent):
    ticket_id: UUID
    closed_by: str             # actor id
    closed_by_role: str        # "customer" | "agent" | "manager" | "system"
    closed_at: datetime


@dataclass
class TicketReopened(DomainEvent):
    ticket_id: UUID
    reopened_by: str           # customer_id
    reopened_at: datetime
    new_sla_deadline: datetime


@dataclass
class SLABreached(DomainEvent):
    ticket_id: UUID
    breached_at: datetime


@dataclass
class AutoReassignmentTriggered(DomainEvent):
    ticket_id: UUID
    previous_agent_id: str
    triggered_at: datetime


@dataclass
class TicketOpenedByAgent(DomainEvent):
    """
    Emitted when an agent explicitly opens/acknowledges an escalated ticket.
    This is the event that stops the auto-reassign clock.
    """
    ticket_id: UUID
    agent_id: str
    opened_at: datetime
