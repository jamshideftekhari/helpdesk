from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from ticket.domain.value_objects import MessageAuthor, Priority, TicketStatus


# ------------------------------------------------------------------ #
# Requests                                                            #
# ------------------------------------------------------------------ #

class OpenTicketRequest(BaseModel):
    customer_id: str
    priority: Priority
    subject: str
    body: str


class AppendMessageRequest(BaseModel):
    author: MessageAuthor
    author_id: str
    body: str


class AcknowledgeRequest(BaseModel):
    agent_id: str


class EscalateRequest(BaseModel):
    escalated_by: str


class CloseRequest(BaseModel):
    closed_by: str
    closed_by_role: str


class ReopenRequest(BaseModel):
    reopened_by: str


# ------------------------------------------------------------------ #
# Responses                                                           #
# ------------------------------------------------------------------ #

class MessageResponse(BaseModel):
    author: MessageAuthor
    author_id: str
    body: str


class TicketResponse(BaseModel):
    id: UUID
    customer_id: str
    agent_id: str | None
    priority: Priority
    status: TicketStatus
    subject: str
    messages: list[MessageResponse]
    sla_deadline: datetime | None
    auto_reassign_deadline: datetime | None
    last_customer_reply_at: datetime | None
    closed_at: datetime | None
