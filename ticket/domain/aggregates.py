from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from shared.base_aggregate import AggregateRoot
from ticket.domain.events import (
    AutoReassignmentTriggered,
    MessageAppended,
    SLABreached,
    TicketClosed,
    TicketEscalated,
    TicketOpened,
    TicketOpenedByAgent,
    TicketReopened,
)
from ticket.domain.value_objects import (
    CUSTOMER_INACTIVITY_WINDOW,
    REOPEN_WINDOW,
    Message,
    MessageAuthor,
    Priority,
    SLAPolicy,
    TicketStatus,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TicketError(Exception):
    """Raised when a command violates a domain invariant."""
    pass


class Ticket(AggregateRoot):
    """
    The central aggregate of the help desk domain.

    All business rules live here. No rule is enforced anywhere else.
    The aggregate only exposes commands (methods that change state)
    and events (facts about what happened). It never exposes raw
    mutable state to the outside world.
    """

    def __init__(self) -> None:
        super().__init__()
        self.id: UUID = uuid4()
        self.customer_id: str = ""
        self.agent_id: str | None = None
        self.priority: Priority = Priority.LOW
        self.status: TicketStatus = TicketStatus.OPEN
        self.subject: str = ""
        self.messages: list[Message] = []
        self.sla_deadline: datetime | None = None
        self.auto_reassign_deadline: datetime | None = None
        self.last_customer_reply_at: datetime | None = None
        self.closed_at: datetime | None = None
        self._sla_policy: SLAPolicy | None = None

    # ------------------------------------------------------------------ #
    # Factory                                                              #
    # ------------------------------------------------------------------ #

    @classmethod
    def open(
        cls,
        customer_id: str,
        priority: Priority,
        subject: str,
        body: str,
        now: datetime | None = None,
    ) -> "Ticket":
        """
        The only way to create a new ticket.
        Enforces that every ticket starts with a subject and a body.
        """
        if not subject.strip():
            raise TicketError("Ticket subject cannot be empty.")
        if not body.strip():
            raise TicketError("Ticket body cannot be empty.")

        now = now or _now()
        ticket = cls()
        ticket.customer_id = customer_id
        ticket.priority = priority
        ticket.subject = subject
        ticket._sla_policy = SLAPolicy(priority)

        sla_deadline = now + ticket._sla_policy.response_window
        ticket.sla_deadline = sla_deadline
        ticket.last_customer_reply_at = now

        first_message = Message(
            author=MessageAuthor.CUSTOMER,
            author_id=customer_id,
            body=body,
        )
        ticket.messages.append(first_message)

        ticket._raise_event(
            TicketOpened(
                ticket_id=ticket.id,
                customer_id=customer_id,
                priority=priority,
                subject=subject,
                body=body,
                sla_deadline=sla_deadline,
                opened_at=now,
            )
        )
        return ticket

    # ------------------------------------------------------------------ #
    # Commands                                                             #
    # ------------------------------------------------------------------ #

    def append_message(
        self,
        author: MessageAuthor,
        author_id: str,
        body: str,
        now: datetime | None = None,
    ) -> None:
        """
        Both customer and agent can append messages.
        - A customer reply resets the 7-day inactivity clock.
        - An agent reply resets the SLA clock.
        Closed tickets cannot receive new messages.
        """
        if self.status == TicketStatus.CLOSED:
            raise TicketError("Cannot append a message to a closed ticket.")
        if not body.strip():
            raise TicketError("Message body cannot be empty.")

        now = now or _now()
        message = Message(author=author, author_id=author_id, body=body)
        self.messages.append(message)

        if author == MessageAuthor.CUSTOMER:
            self.last_customer_reply_at = now

        if author == MessageAuthor.AGENT and self._sla_policy:
            self.sla_deadline = now + self._sla_policy.response_window

        self._raise_event(
            MessageAppended(
                ticket_id=self.id,
                author=author,
                author_id=author_id,
                body=body,
                appended_at=now,
            )
        )

    def acknowledge(self, agent_id: str, now: datetime | None = None) -> None:
        """
        An agent explicitly opens/acknowledges an escalated ticket.
        This stops the auto-reassign clock.
        Only meaningful on escalated tickets — silently ignored otherwise.
        """
        if self.status != TicketStatus.ESCALATED:
            return

        now = now or _now()
        self._raise_event(
            TicketOpenedByAgent(
                ticket_id=self.id,
                agent_id=agent_id,
                opened_at=now,
            )
        )
        self.auto_reassign_deadline = None

    def escalate(self, escalated_by: str, now: datetime | None = None) -> None:
        """
        Customer escalates after SLA breach.
        Rules:
          - Only OPEN tickets can be escalated.
          - Reduces the remaining response window by 33%.
          - Sets an auto-reassign deadline at 50% of the reduced window.
        """
        if self.status != TicketStatus.OPEN:
            raise TicketError(
                f"Only open tickets can be escalated. Current status: {self.status}."
            )
        if not self.sla_deadline:
            raise TicketError("Ticket has no SLA deadline set.")

        now = now or _now()
        policy = self._sla_policy or SLAPolicy(self.priority)

        remaining = self.sla_deadline - now
        if remaining.total_seconds() <= 0:
            remaining_window = policy.response_window
        else:
            remaining_window = remaining

        reduced = policy.reduced_window(remaining_window)
        new_deadline = now + reduced
        auto_reassign_deadline = now + policy.auto_reassign_window(reduced)

        self.status = TicketStatus.ESCALATED
        self.sla_deadline = new_deadline
        self.auto_reassign_deadline = auto_reassign_deadline

        self._raise_event(
            TicketEscalated(
                ticket_id=self.id,
                escalated_by=escalated_by,
                original_deadline=self.sla_deadline,
                new_deadline=new_deadline,
                auto_reassign_deadline=auto_reassign_deadline,
                escalated_at=now,
            )
        )

    def close(
        self,
        closed_by: str,
        closed_by_role: str,
        now: datetime | None = None,
    ) -> None:
        """
        Closes the ticket.
        Rules:
          - Escalated tickets can only be closed by 'customer' or 'manager'.
          - The 'system' may only close non-escalated tickets (inactivity).
          - Agents may only close non-escalated tickets.
        """
        if self.status == TicketStatus.CLOSED:
            raise TicketError("Ticket is already closed.")

        if self.status == TicketStatus.ESCALATED:
            if closed_by_role not in ("customer", "manager"):
                raise TicketError(
                    "Escalated tickets can only be closed by the customer "
                    "or the agent's manager."
                )

        now = now or _now()
        self.status = TicketStatus.CLOSED
        self.closed_at = now

        self._raise_event(
            TicketClosed(
                ticket_id=self.id,
                closed_by=closed_by,
                closed_by_role=closed_by_role,
                closed_at=now,
            )
        )

    def reopen(self, reopened_by: str, now: datetime | None = None) -> None:
        """
        Customer reopens a recently closed ticket.
        Rules:
          - Ticket must be CLOSED.
          - Must have been closed within the past 7 days.
          - Reopening resets the SLA clock.
        """
        if self.status != TicketStatus.CLOSED:
            raise TicketError(
                f"Only closed tickets can be reopened. Current status: {self.status}."
            )
        if not self.closed_at:
            raise TicketError("Ticket has no recorded close time.")

        now = now or _now()
        age = now - self.closed_at
        if age > REOPEN_WINDOW:
            raise TicketError(
                f"Ticket can only be reopened within {REOPEN_WINDOW.days} days of closure. "
                f"This ticket was closed {age.days} days ago."
            )

        policy = self._sla_policy or SLAPolicy(self.priority)
        new_sla_deadline = now + policy.response_window

        self.status = TicketStatus.OPEN
        self.sla_deadline = new_sla_deadline
        self.closed_at = None

        self._raise_event(
            TicketReopened(
                ticket_id=self.id,
                reopened_by=reopened_by,
                reopened_at=now,
                new_sla_deadline=new_sla_deadline,
            )
        )

    # ------------------------------------------------------------------ #
    # System-triggered transitions                                         #
    # These are called by the scheduler, not by a human actor.            #
    # ------------------------------------------------------------------ #

    def mark_sla_breached(self, now: datetime | None = None) -> None:
        """Called by the scheduler when sla_deadline has passed."""
        now = now or _now()
        self._raise_event(SLABreached(ticket_id=self.id, breached_at=now))

    def auto_close(self, now: datetime | None = None) -> None:
        """
        Called by the scheduler when the customer inactivity window expires.
        Escalated tickets are immune.
        """
        if self.status == TicketStatus.ESCALATED:
            raise TicketError("Escalated tickets cannot be auto-closed.")

        self.close(closed_by="system", closed_by_role="system", now=now)

    def trigger_auto_reassignment(self, now: datetime | None = None) -> None:
        """
        Called by the scheduler when auto_reassign_deadline has passed
        and the agent has not acknowledged the escalated ticket.
        """
        if self.status != TicketStatus.ESCALATED:
            raise TicketError(
                "Auto-reassignment only applies to escalated tickets."
            )
        if not self.agent_id:
            raise TicketError("Ticket has no assigned agent to reassign from.")

        now = now or _now()
        previous_agent_id = self.agent_id
        self.agent_id = None
        self.auto_reassign_deadline = None

        self._raise_event(
            AutoReassignmentTriggered(
                ticket_id=self.id,
                previous_agent_id=previous_agent_id,
                triggered_at=now,
            )
        )

    # ------------------------------------------------------------------ #
    # Queries (read-only helpers — no state change, no events)            #
    # ------------------------------------------------------------------ #

    def is_past_inactivity_window(self, now: datetime | None = None) -> bool:
        """True if the customer has not replied within 7 days."""
        if not self.last_customer_reply_at:
            return False
        now = now or _now()
        return (now - self.last_customer_reply_at) > CUSTOMER_INACTIVITY_WINDOW

    def is_past_sla(self, now: datetime | None = None) -> bool:
        if not self.sla_deadline:
            return False
        now = now or _now()
        return now > self.sla_deadline

    def is_past_auto_reassign_deadline(self, now: datetime | None = None) -> bool:
        if not self.auto_reassign_deadline:
            return False
        now = now or _now()
        return now > self.auto_reassign_deadline
