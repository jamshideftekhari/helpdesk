"""
Interactive demo for the Ticket domain model.

Run with:
    python demo.py

No database, no HTTP — pure domain layer only.
All times are simulated so you can fast-forward through
SLA windows, escalation, and closure scenarios.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta, timezone
from textwrap import dedent
from uuid import UUID

from ticket.domain.aggregates import Ticket, TicketError
from ticket.domain.value_objects import MessageAuthor, Priority, TicketStatus


# ── Simulated clock ────────────────────────────────────────────────────────────
# All actions go through this clock. Use  t  /  f  commands to move time forward.

_current_time = datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc)

def now() -> datetime:
    return _current_time

def advance(hours: float) -> None:
    global _current_time
    _current_time += timedelta(hours=hours)


# ── State ──────────────────────────────────────────────────────────────────────

tickets: dict[str, Ticket] = {}   # short_id → Ticket
_counter = 1

def short_id(ticket: Ticket) -> str:
    return str(ticket.id)[:8]


# ── Display helpers ────────────────────────────────────────────────────────────

COLORS = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "gray":    "\033[90m",
    "green":   "\033[92m",
    "yellow":  "\033[93m",
    "red":     "\033[91m",
    "blue":    "\033[94m",
    "cyan":    "\033[96m",
    "magenta": "\033[95m",
}

def c(color: str, text: str) -> str:
    return f"{COLORS.get(color,'')}{text}{COLORS['reset']}"

STATUS_COLOR = {
    TicketStatus.OPEN:      "green",
    TicketStatus.ESCALATED: "yellow",
    TicketStatus.CLOSED:    "gray",
}

PRIORITY_COLOR = {
    Priority.LOW:    "blue",
    Priority.MEDIUM: "cyan",
    Priority.HIGH:   "yellow",
    Priority.URGENT: "red",
}

def fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return c("gray", "—")
    return dt.strftime("%H:%M  %d %b")

def fmt_delta(dt: datetime | None) -> str:
    if dt is None:
        return c("gray", "—")
    diff = dt - now()
    total = int(diff.total_seconds())
    if total < 0:
        return c("red", f"BREACHED {abs(total)//3600}h ago")
    h, m = divmod(total // 60, 60)
    return c("green" if h > 1 else "yellow", f"in {h}h {m}m")


def print_ticket(ticket: Ticket) -> None:
    sid = short_id(ticket)
    status = ticket.status
    sc = STATUS_COLOR[status]
    pc = PRIORITY_COLOR[ticket.priority]

    print()
    print(c("bold", f"  Ticket {sid}"))
    print(f"  {'Status':<22} {c(sc, status.value.upper())}")
    print(f"  {'Priority':<22} {c(pc, ticket.priority.value.upper())}")
    print(f"  {'Customer':<22} {ticket.customer_id}")
    print(f"  {'Agent':<22} {ticket.agent_id or c('gray','unassigned')}")
    print(f"  {'Subject':<22} {ticket.subject}")
    print(f"  {'SLA deadline':<22} {fmt_time(ticket.sla_deadline)}  ({fmt_delta(ticket.sla_deadline)})")

    if ticket.auto_reassign_deadline:
        print(f"  {'Auto-reassign by':<22} {fmt_time(ticket.auto_reassign_deadline)}  ({fmt_delta(ticket.auto_reassign_deadline)})")

    if ticket.closed_at:
        print(f"  {'Closed at':<22} {fmt_time(ticket.closed_at)}")

    print(f"  {'Messages':<22} {len(ticket.messages)}")
    print()

    for i, msg in enumerate(ticket.messages, 1):
        author_label = c("cyan", msg.author_id) if msg.author == MessageAuthor.CUSTOMER else c("magenta", msg.author_id)
        print(f"    [{i}] {author_label}: {msg.body}")
    print()

    events = ticket._events
    if events:
        print(c("gray", f"  Pending events: {[type(e).__name__ for e in events]}"))
    print()


def print_all_tickets() -> None:
    if not tickets:
        print(c("gray", "  No tickets yet. Use  open  to create one.\n"))
        return
    for ticket in tickets.values():
        print_ticket(ticket)


def print_help() -> None:
    print(dedent(f"""
  {c("bold", "Commands")}

  {c("cyan", "open")}   <customer> <priority> <subject>     Open a new ticket
              priorities: low  medium  high  urgent
              e.g.  open alice high "Login broken"

  {c("cyan", "msg")}    <ticket_id> <role> <author> <text>   Append a message
              role: customer | agent
              e.g.  msg a1b2c3d4 agent support1 "Working on it"

  {c("cyan", "assign")} <ticket_id> <agent_id>              Assign an agent to a ticket

  {c("cyan", "ack")}    <ticket_id> <agent_id>              Agent acknowledges escalated ticket
                                               (stops auto-reassign clock)

  {c("cyan", "escalate")} <ticket_id> <customer_id>         Customer escalates after SLA breach

  {c("cyan", "close")}  <ticket_id> <actor_id> <role>       Close a ticket
              role: customer | agent | manager | system
              e.g.  close a1b2c3d4 alice customer

  {c("cyan", "reopen")} <ticket_id> <customer_id>           Reopen a closed ticket

  {c("cyan", "breach")} <ticket_id>                         Simulate SLA breach (system)

  {c("cyan", "reassign")} <ticket_id>                       Trigger auto-reassignment (system)

  {c("cyan", "show")}   [ticket_id]                         Show one ticket, or all tickets

  {c("cyan", "time")}                                       Show the current simulated time

  {c("cyan", "advance")} <hours>                            Advance the simulated clock
              e.g.  advance 5

  {c("cyan", "scenario")} <name>                            Run a pre-built scenario
              scenarios: basic  escalation  autoclose  reopen

  {c("cyan", "help")}                                       Show this help

  {c("cyan", "quit")}                                       Exit
    """))


def resolve_ticket(token: str) -> Ticket | None:
    """Match a ticket by its short ID prefix."""
    matches = [t for sid, t in tickets.items() if sid.startswith(token)]
    if not matches:
        print(c("red", f"  No ticket found matching '{token}'"))
        return None
    if len(matches) > 1:
        print(c("red", f"  Ambiguous ID — matches: {[short_id(t) for t in matches]}"))
        return None
    return matches[0]


# ── Command handlers ───────────────────────────────────────────────────────────

def cmd_open(parts: list[str]) -> None:
    if len(parts) < 4:
        print(c("red", "  Usage: open <customer> <priority> <subject>"))
        return
    customer_id = parts[1]
    priority_str = parts[2].lower()
    subject = " ".join(parts[3:]).strip('"')

    try:
        priority = Priority(priority_str)
    except ValueError:
        print(c("red", f"  Unknown priority '{priority_str}'. Use: low medium high urgent"))
        return

    try:
        ticket = Ticket.open(
            customer_id=customer_id,
            priority=priority,
            subject=subject,
            body=f"(opened via demo — no body provided)",
            now=now(),
        )
        events = ticket.collect_events()
        sid = short_id(ticket)
        tickets[sid] = ticket
        print(c("green", f"  Ticket {sid} opened."))
        print(c("gray",  f"  Events raised: {[type(e).__name__ for e in events]}"))
        print_ticket(ticket)
    except TicketError as e:
        print(c("red", f"  Error: {e}"))


def cmd_msg(parts: list[str]) -> None:
    if len(parts) < 5:
        print(c("red", "  Usage: msg <ticket_id> <role> <author_id> <message text>"))
        return
    ticket = resolve_ticket(parts[1])
    if not ticket:
        return
    role = parts[2].lower()
    author_id = parts[3]
    body = " ".join(parts[4:]).strip('"')

    role_map = {
        "customer": MessageAuthor.CUSTOMER,
        "agent":    MessageAuthor.AGENT,
        "system":   MessageAuthor.SYSTEM,
    }
    if role not in role_map:
        print(c("red", f"  Unknown role '{role}'. Use: customer agent system"))
        return

    try:
        ticket.append_message(role_map[role], author_id, body, now=now())
        events = ticket.collect_events()
        print(c("green", f"  Message appended."))
        print(c("gray",  f"  Events raised: {[type(e).__name__ for e in events]}"))
        print_ticket(ticket)
    except TicketError as e:
        print(c("red", f"  Error: {e}"))


def cmd_assign(parts: list[str]) -> None:
    if len(parts) < 3:
        print(c("red", "  Usage: assign <ticket_id> <agent_id>"))
        return
    ticket = resolve_ticket(parts[1])
    if not ticket:
        return
    ticket.agent_id = parts[2]
    print(c("green", f"  Agent {parts[2]} assigned to ticket {short_id(ticket)}."))


def cmd_ack(parts: list[str]) -> None:
    if len(parts) < 3:
        print(c("red", "  Usage: ack <ticket_id> <agent_id>"))
        return
    ticket = resolve_ticket(parts[1])
    if not ticket:
        return
    try:
        ticket.acknowledge(parts[2], now=now())
        events = ticket.collect_events()
        print(c("green", f"  Ticket acknowledged by {parts[2]}."))
        print(c("gray",  f"  Events raised: {[type(e).__name__ for e in events]}"))
        print_ticket(ticket)
    except TicketError as e:
        print(c("red", f"  Error: {e}"))


def cmd_escalate(parts: list[str]) -> None:
    if len(parts) < 3:
        print(c("red", "  Usage: escalate <ticket_id> <customer_id>"))
        return
    ticket = resolve_ticket(parts[1])
    if not ticket:
        return
    try:
        ticket.escalate(parts[2], now=now())
        events = ticket.collect_events()
        print(c("yellow", f"  Ticket escalated."))
        print(c("gray",   f"  Events raised: {[type(e).__name__ for e in events]}"))
        print_ticket(ticket)
    except TicketError as e:
        print(c("red", f"  Error: {e}"))


def cmd_close(parts: list[str]) -> None:
    if len(parts) < 4:
        print(c("red", "  Usage: close <ticket_id> <actor_id> <role>"))
        return
    ticket = resolve_ticket(parts[1])
    if not ticket:
        return
    try:
        ticket.close(parts[2], parts[3], now=now())
        events = ticket.collect_events()
        print(c("gray",  f"  Ticket closed."))
        print(c("gray",  f"  Events raised: {[type(e).__name__ for e in events]}"))
        print_ticket(ticket)
    except TicketError as e:
        print(c("red", f"  Error: {e}"))


def cmd_reopen(parts: list[str]) -> None:
    if len(parts) < 3:
        print(c("red", "  Usage: reopen <ticket_id> <customer_id>"))
        return
    ticket = resolve_ticket(parts[1])
    if not ticket:
        return
    try:
        ticket.reopen(parts[2], now=now())
        events = ticket.collect_events()
        print(c("green", f"  Ticket reopened."))
        print(c("gray",  f"  Events raised: {[type(e).__name__ for e in events]}"))
        print_ticket(ticket)
    except TicketError as e:
        print(c("red", f"  Error: {e}"))


def cmd_breach(parts: list[str]) -> None:
    if len(parts) < 2:
        print(c("red", "  Usage: breach <ticket_id>"))
        return
    ticket = resolve_ticket(parts[1])
    if not ticket:
        return
    ticket.mark_sla_breached(now=now())
    events = ticket.collect_events()
    print(c("red",  f"  SLA breached."))
    print(c("gray", f"  Events raised: {[type(e).__name__ for e in events]}"))


def cmd_reassign(parts: list[str]) -> None:
    if len(parts) < 2:
        print(c("red", "  Usage: reassign <ticket_id>"))
        return
    ticket = resolve_ticket(parts[1])
    if not ticket:
        return
    try:
        ticket.trigger_auto_reassignment(now=now())
        events = ticket.collect_events()
        print(c("yellow", f"  Auto-reassignment triggered."))
        print(c("gray",   f"  Events raised: {[type(e).__name__ for e in events]}"))
        print_ticket(ticket)
    except TicketError as e:
        print(c("red", f"  Error: {e}"))


def cmd_show(parts: list[str]) -> None:
    if len(parts) >= 2:
        ticket = resolve_ticket(parts[1])
        if ticket:
            print_ticket(ticket)
    else:
        print_all_tickets()


def cmd_time(_: list[str]) -> None:
    print(c("cyan", f"  Simulated time: {now().strftime('%H:%M  %d %b %Y')} UTC\n"))


def cmd_advance(parts: list[str]) -> None:
    if len(parts) < 2:
        print(c("red", "  Usage: advance <hours>"))
        return
    try:
        h = float(parts[1])
        advance(h)
        print(c("cyan", f"  Clock advanced {h}h → {now().strftime('%H:%M  %d %b %Y')} UTC\n"))
    except ValueError:
        print(c("red", "  Hours must be a number."))


# ── Pre-built scenarios ────────────────────────────────────────────────────────

def scenario_basic() -> None:
    print(c("bold", "\n  Scenario: basic ticket lifecycle\n"))
    ticket = Ticket.open("alice", Priority.MEDIUM, "Cannot export PDF", "Export button does nothing.", now=now())
    ticket.agent_id = "bob"
    ticket.collect_events()
    sid = short_id(ticket)
    tickets[sid] = ticket
    print(c("gray", "  → alice opens ticket, bob is assigned"))
    print_ticket(ticket)

    input(c("gray", "  Press Enter to append agent reply..."))
    advance(1)
    ticket.append_message(MessageAuthor.AGENT, "bob", "Hi Alice, I am looking into this now.", now=now())
    ticket.collect_events()
    print(c("gray", "  → bob replies (SLA clock resets)"))
    print_ticket(ticket)

    input(c("gray", "  Press Enter to close ticket..."))
    advance(0.5)
    ticket.close("alice", "customer", now=now())
    ticket.collect_events()
    print(c("gray", "  → alice closes the ticket"))
    print_ticket(ticket)


def scenario_escalation() -> None:
    print(c("bold", "\n  Scenario: SLA breach and escalation\n"))
    ticket = Ticket.open("carol", Priority.HIGH, "Payment failing", "All payments return 500.", now=now())
    ticket.agent_id = "dave"
    ticket.collect_events()
    sid = short_id(ticket)
    tickets[sid] = ticket
    print(c("gray", "  → carol opens HIGH priority ticket, dave assigned"))
    print(c("gray", f"  → SLA window: 4 hours"))
    print_ticket(ticket)

    input(c("gray", "  Press Enter to advance 5 hours (past SLA)..."))
    advance(5)
    ticket.mark_sla_breached(now=now())
    ticket.collect_events()
    print(c("red", "  → SLA breached — carol can now escalate"))

    input(c("gray", "  Press Enter to escalate..."))
    ticket.escalate("carol", now=now())
    ticket.collect_events()
    print(c("yellow", "  → ticket escalated, deadline reduced by 33%, auto-reassign clock started"))
    print_ticket(ticket)

    input(c("gray", "  Press Enter to have dave acknowledge..."))
    advance(0.5)
    ticket.acknowledge("dave", now=now())
    ticket.collect_events()
    print(c("green", "  → dave acknowledges — auto-reassign clock stopped"))
    print_ticket(ticket)

    input(c("gray", "  Press Enter to have manager close..."))
    advance(1)
    ticket.close("mgr1", "manager", now=now())
    ticket.collect_events()
    print(c("gray", "  → manager closes the escalated ticket"))
    print_ticket(ticket)


def scenario_autoclose() -> None:
    print(c("bold", "\n  Scenario: 7-day inactivity auto-close\n"))
    ticket = Ticket.open("eve", Priority.LOW, "Slow dashboard", "Dashboard takes 10s to load.", now=now())
    ticket.agent_id = "frank"
    ticket.collect_events()
    sid = short_id(ticket)
    tickets[sid] = ticket
    print(c("gray", "  → eve opens ticket, frank replies"))
    ticket.append_message(MessageAuthor.AGENT, "frank", "Can you share a screenshot?", now=now())
    ticket.collect_events()
    print_ticket(ticket)

    input(c("gray", "  Press Enter to advance 8 days (no reply from eve)..."))
    advance(24 * 8)
    print(c("gray", f"  → inactivity check: {ticket.is_past_inactivity_window(now=now())}"))
    ticket.auto_close(now=now())
    ticket.collect_events()
    print(c("gray",  "  → system auto-closes ticket"))
    print_ticket(ticket)


def scenario_reopen() -> None:
    print(c("bold", "\n  Scenario: customer reopens a recently closed ticket\n"))
    ticket = Ticket.open("grace", Priority.MEDIUM, "Wrong invoice", "Invoice shows wrong amount.", now=now())
    ticket.agent_id = "henry"
    ticket.collect_events()
    sid = short_id(ticket)
    tickets[sid] = ticket
    ticket.close("henry", "agent", now=now())
    ticket.collect_events()
    print(c("gray", "  → ticket opened and closed by agent"))
    print_ticket(ticket)

    input(c("gray", "  Press Enter to advance 3 days and reopen..."))
    advance(24 * 3)
    ticket.reopen("grace", now=now())
    ticket.collect_events()
    print(c("green", "  → grace reopens within 7-day window"))
    print_ticket(ticket)

    input(c("gray", "  Press Enter to try reopening after 8 more days (should fail)..."))
    ticket.close("grace", "customer", now=now())
    ticket.collect_events()
    advance(24 * 8)
    try:
        ticket.reopen("grace", now=now())
    except TicketError as e:
        print(c("red", f"  → Correctly rejected: {e}"))


SCENARIOS = {
    "basic":       scenario_basic,
    "escalation":  scenario_escalation,
    "autoclose":   scenario_autoclose,
    "reopen":      scenario_reopen,
}

def cmd_scenario(parts: list[str]) -> None:
    if len(parts) < 2 or parts[1] not in SCENARIOS:
        names = "  ".join(SCENARIOS.keys())
        print(c("red", f"  Available scenarios: {names}"))
        return
    SCENARIOS[parts[1]]()


# ── Main REPL ──────────────────────────────────────────────────────────────────

COMMANDS = {
    "open":     cmd_open,
    "msg":      cmd_msg,
    "assign":   cmd_assign,
    "ack":      cmd_ack,
    "escalate": cmd_escalate,
    "close":    cmd_close,
    "reopen":   cmd_reopen,
    "breach":   cmd_breach,
    "reassign": cmd_reassign,
    "show":     cmd_show,
    "time":     cmd_time,
    "advance":  cmd_advance,
    "scenario": cmd_scenario,
    "help":     lambda _: print_help(),
}

def main() -> None:
    print()
    print(c("bold", "  Help Desk — Domain Model Explorer"))
    print(c("gray", "  Phase 1 · Pure domain layer · No database"))
    print(c("gray", f"  Simulated time starts at: {now().strftime('%H:%M  %d %b %Y')} UTC"))
    print()
    print(c("gray", "  Type  help  to see all commands."))
    print(c("gray", "  Type  scenario basic  for a guided walkthrough."))
    print()

    while True:
        try:
            raw = input(c("bold", "  > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(c("gray", "\n  Bye.\n"))
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            print(c("gray", "\n  Bye.\n"))
            break
        elif cmd in COMMANDS:
            COMMANDS[cmd](parts)
        else:
            print(c("red", f"  Unknown command '{cmd}'. Type  help  for a list.\n"))


if __name__ == "__main__":
    main()
