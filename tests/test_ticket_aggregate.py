"""
Unit tests for the Ticket aggregate.

All tests are pure — no database, no HTTP, no framework.
Time is always injected via the `now` parameter so tests are deterministic.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timedelta, timezone

from ticket.domain.aggregates import Ticket, TicketError
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
    MessageAuthor,
    Priority,
    TicketStatus,
    SLA_WINDOWS,
    REOPEN_WINDOW,
)


def t(offset_hours: float = 0) -> datetime:
    """Helper: a fixed UTC base time, optionally offset by hours."""
    base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(hours=offset_hours)


# ══════════════════════════════════════════════════════════════════════════
# Opening a ticket
# ══════════════════════════════════════════════════════════════════════════

class TestOpenTicket:

    def test_open_creates_ticket_with_correct_state(self):
        ticket = Ticket.open(
            customer_id="cust-1",
            priority=Priority.HIGH,
            subject="Login broken",
            body="I cannot log in since this morning.",
            now=t(),
        )
        assert ticket.customer_id == "cust-1"
        assert ticket.priority == Priority.HIGH
        assert ticket.status == TicketStatus.OPEN
        assert ticket.subject == "Login broken"
        assert len(ticket.messages) == 1

    def test_open_sets_sla_deadline_from_priority(self):
        ticket = Ticket.open(
            customer_id="cust-1",
            priority=Priority.HIGH,
            subject="Issue",
            body="Details.",
            now=t(),
        )
        expected_deadline = t() + SLA_WINDOWS[Priority.HIGH]
        assert ticket.sla_deadline == expected_deadline

    def test_open_raises_ticket_opened_event(self):
        ticket = Ticket.open("c1", Priority.LOW, "Sub", "Body", now=t())
        events = ticket.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], TicketOpened)
        assert events[0].customer_id == "c1"

    def test_open_rejects_empty_subject(self):
        with pytest.raises(TicketError, match="subject cannot be empty"):
            Ticket.open("c1", Priority.LOW, "  ", "Body", now=t())

    def test_open_rejects_empty_body(self):
        with pytest.raises(TicketError, match="body cannot be empty"):
            Ticket.open("c1", Priority.LOW, "Subject", "   ", now=t())

    def test_collect_events_clears_after_call(self):
        ticket = Ticket.open("c1", Priority.LOW, "Sub", "Body", now=t())
        ticket.collect_events()
        assert ticket.collect_events() == []


# ══════════════════════════════════════════════════════════════════════════
# Appending messages
# ══════════════════════════════════════════════════════════════════════════

class TestAppendMessage:

    def setup_method(self):
        self.ticket = Ticket.open("cust-1", Priority.MEDIUM, "Sub", "Body", now=t())
        self.ticket.collect_events()

    def test_agent_can_append_message(self):
        self.ticket.append_message(MessageAuthor.AGENT, "agent-1", "How can I help?", now=t(1))
        assert len(self.ticket.messages) == 2

    def test_customer_can_append_message(self):
        self.ticket.append_message(MessageAuthor.CUSTOMER, "cust-1", "Still broken.", now=t(1))
        assert len(self.ticket.messages) == 2

    def test_customer_reply_resets_inactivity_clock(self):
        self.ticket.append_message(MessageAuthor.CUSTOMER, "cust-1", "Reply.", now=t(3))
        assert self.ticket.last_customer_reply_at == t(3)

    def test_agent_reply_resets_sla_clock(self):
        self.ticket.append_message(MessageAuthor.AGENT, "agent-1", "Working on it.", now=t(2))
        expected = t(2) + SLA_WINDOWS[Priority.MEDIUM]
        assert self.ticket.sla_deadline == expected

    def test_append_raises_message_appended_event(self):
        self.ticket.append_message(MessageAuthor.AGENT, "agent-1", "Hi", now=t(1))
        events = self.ticket.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], MessageAppended)

    def test_cannot_append_to_closed_ticket(self):
        self.ticket.close("cust-1", "customer", now=t(1))
        with pytest.raises(TicketError, match="closed ticket"):
            self.ticket.append_message(MessageAuthor.CUSTOMER, "cust-1", "Hello?", now=t(2))

    def test_cannot_append_empty_body(self):
        with pytest.raises(TicketError, match="cannot be empty"):
            self.ticket.append_message(MessageAuthor.AGENT, "agent-1", "  ", now=t(1))


# ══════════════════════════════════════════════════════════════════════════
# Escalation
# ══════════════════════════════════════════════════════════════════════════

class TestEscalation:

    def setup_method(self):
        self.ticket = Ticket.open("cust-1", Priority.HIGH, "Sub", "Body", now=t())
        self.ticket.agent_id = "agent-1"
        self.ticket.collect_events()

    def test_escalate_changes_status_to_escalated(self):
        self.ticket.escalate("cust-1", now=t(5))
        assert self.ticket.status == TicketStatus.ESCALATED

    def test_escalate_reduces_deadline_by_33_percent(self):
        original_window = SLA_WINDOWS[Priority.HIGH]
        self.ticket.escalate("cust-1", now=t())
        reduced_window = original_window * (1 - 0.33)
        expected_deadline = t() + reduced_window
        assert abs((self.ticket.sla_deadline - expected_deadline).total_seconds()) < 1

    def test_escalate_sets_auto_reassign_deadline(self):
        self.ticket.escalate("cust-1", now=t())
        assert self.ticket.auto_reassign_deadline is not None
        assert self.ticket.auto_reassign_deadline < self.ticket.sla_deadline

    def test_escalate_raises_ticket_escalated_event(self):
        self.ticket.escalate("cust-1", now=t(5))
        events = self.ticket.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], TicketEscalated)
        assert events[0].escalated_by == "cust-1"

    def test_cannot_escalate_closed_ticket(self):
        self.ticket.close("cust-1", "customer", now=t(1))
        with pytest.raises(TicketError, match="Only open tickets"):
            self.ticket.escalate("cust-1", now=t(2))

    def test_cannot_escalate_already_escalated_ticket(self):
        self.ticket.escalate("cust-1", now=t(5))
        with pytest.raises(TicketError, match="Only open tickets"):
            self.ticket.escalate("cust-1", now=t(6))


# ══════════════════════════════════════════════════════════════════════════
# Closure rules
# ══════════════════════════════════════════════════════════════════════════

class TestClosure:

    def setup_method(self):
        self.ticket = Ticket.open("cust-1", Priority.LOW, "Sub", "Body", now=t())
        self.ticket.agent_id = "agent-1"
        self.ticket.collect_events()

    def test_customer_can_close_open_ticket(self):
        self.ticket.close("cust-1", "customer", now=t(1))
        assert self.ticket.status == TicketStatus.CLOSED

    def test_agent_can_close_open_ticket(self):
        self.ticket.close("agent-1", "agent", now=t(1))
        assert self.ticket.status == TicketStatus.CLOSED

    def test_system_can_auto_close_open_ticket(self):
        self.ticket.auto_close(now=t(1))
        assert self.ticket.status == TicketStatus.CLOSED

    def test_close_raises_ticket_closed_event(self):
        self.ticket.close("cust-1", "customer", now=t(1))
        events = self.ticket.collect_events()
        assert any(isinstance(e, TicketClosed) for e in events)

    def test_cannot_close_already_closed_ticket(self):
        self.ticket.close("cust-1", "customer", now=t(1))
        with pytest.raises(TicketError, match="already closed"):
            self.ticket.close("cust-1", "customer", now=t(2))

    def test_agent_cannot_close_escalated_ticket(self):
        self.ticket.escalate("cust-1", now=t(5))
        self.ticket.collect_events()
        with pytest.raises(TicketError, match="manager"):
            self.ticket.close("agent-1", "agent", now=t(6))

    def test_system_cannot_auto_close_escalated_ticket(self):
        self.ticket.escalate("cust-1", now=t(5))
        self.ticket.collect_events()
        with pytest.raises(TicketError, match="Escalated tickets cannot be auto-closed"):
            self.ticket.auto_close(now=t(6))

    def test_customer_can_close_escalated_ticket(self):
        self.ticket.escalate("cust-1", now=t(5))
        self.ticket.collect_events()
        self.ticket.close("cust-1", "customer", now=t(6))
        assert self.ticket.status == TicketStatus.CLOSED

    def test_manager_can_close_escalated_ticket(self):
        self.ticket.escalate("cust-1", now=t(5))
        self.ticket.collect_events()
        self.ticket.close("mgr-1", "manager", now=t(6))
        assert self.ticket.status == TicketStatus.CLOSED


# ══════════════════════════════════════════════════════════════════════════
# Reopen
# ══════════════════════════════════════════════════════════════════════════

class TestReopen:

    def setup_method(self):
        self.ticket = Ticket.open("cust-1", Priority.LOW, "Sub", "Body", now=t())
        self.ticket.close("cust-1", "customer", now=t(1))
        self.ticket.collect_events()

    def test_customer_can_reopen_within_7_days(self):
        self.ticket.reopen("cust-1", now=t(2))
        assert self.ticket.status == TicketStatus.OPEN

    def test_reopen_resets_sla_deadline(self):
        self.ticket.reopen("cust-1", now=t(2))
        expected = t(2) + SLA_WINDOWS[Priority.LOW]
        assert self.ticket.sla_deadline == expected

    def test_reopen_raises_ticket_reopened_event(self):
        self.ticket.reopen("cust-1", now=t(2))
        events = self.ticket.collect_events()
        assert any(isinstance(e, TicketReopened) for e in events)

    def test_cannot_reopen_after_7_days(self):
        reopen_time = t(1) + REOPEN_WINDOW + timedelta(hours=1)
        with pytest.raises(TicketError, match="7 days"):
            self.ticket.reopen("cust-1", now=reopen_time)

    def test_cannot_reopen_open_ticket(self):
        open_ticket = Ticket.open("cust-1", Priority.LOW, "Sub", "Body", now=t())
        with pytest.raises(TicketError, match="Only closed tickets"):
            open_ticket.reopen("cust-1", now=t(1))


# ══════════════════════════════════════════════════════════════════════════
# System-triggered transitions (scheduler-driven)
# ══════════════════════════════════════════════════════════════════════════

class TestSchedulerTransitions:

    def test_mark_sla_breached_raises_event(self):
        ticket = Ticket.open("cust-1", Priority.URGENT, "Sub", "Body", now=t())
        ticket.collect_events()
        ticket.mark_sla_breached(now=t(2))
        events = ticket.collect_events()
        assert any(isinstance(e, SLABreached) for e in events)

    def test_trigger_auto_reassignment_clears_agent(self):
        ticket = Ticket.open("cust-1", Priority.HIGH, "Sub", "Body", now=t())
        ticket.agent_id = "agent-1"
        ticket.escalate("cust-1", now=t(5))
        ticket.collect_events()
        ticket.trigger_auto_reassignment(now=t(6))
        assert ticket.agent_id is None

    def test_trigger_auto_reassignment_raises_event(self):
        ticket = Ticket.open("cust-1", Priority.HIGH, "Sub", "Body", now=t())
        ticket.agent_id = "agent-1"
        ticket.escalate("cust-1", now=t(5))
        ticket.collect_events()
        ticket.trigger_auto_reassignment(now=t(6))
        events = ticket.collect_events()
        assert any(isinstance(e, AutoReassignmentTriggered) for e in events)
        assert events[0].previous_agent_id == "agent-1"

    def test_auto_reassignment_fails_on_non_escalated_ticket(self):
        ticket = Ticket.open("cust-1", Priority.HIGH, "Sub", "Body", now=t())
        ticket.agent_id = "agent-1"
        with pytest.raises(TicketError, match="escalated"):
            ticket.trigger_auto_reassignment(now=t(1))

    def test_acknowledge_stops_auto_reassign_clock(self):
        ticket = Ticket.open("cust-1", Priority.HIGH, "Sub", "Body", now=t())
        ticket.agent_id = "agent-1"
        ticket.escalate("cust-1", now=t(5))
        assert ticket.auto_reassign_deadline is not None
        ticket.acknowledge("agent-1", now=t(5.5))
        assert ticket.auto_reassign_deadline is None

    def test_acknowledge_raises_ticket_opened_by_agent_event(self):
        ticket = Ticket.open("cust-1", Priority.HIGH, "Sub", "Body", now=t())
        ticket.agent_id = "agent-1"
        ticket.escalate("cust-1", now=t(5))
        ticket.collect_events()
        ticket.acknowledge("agent-1", now=t(5.5))
        events = ticket.collect_events()
        assert any(isinstance(e, TicketOpenedByAgent) for e in events)

    def test_is_past_inactivity_window(self):
        ticket = Ticket.open("cust-1", Priority.LOW, "Sub", "Body", now=t())
        assert not ticket.is_past_inactivity_window(now=t(24 * 6))
        assert ticket.is_past_inactivity_window(now=t(24 * 8))
