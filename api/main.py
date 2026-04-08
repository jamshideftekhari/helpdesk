from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ticket.domain.aggregates import Ticket, TicketError
from ticket.domain.value_objects import MessageAuthor

from api.schemas import (
    AcknowledgeRequest,
    AppendMessageRequest,
    CloseRequest,
    EscalateRequest,
    MessageResponse,
    OpenTicketRequest,
    ReopenRequest,
    TicketResponse,
)

app = FastAPI(title="Helpdesk API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="webapp"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse("webapp/index.html")


# In-memory store: ticket_id -> Ticket
_store: dict[UUID, Ticket] = {}


def _not_found(ticket_id: UUID) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found.")


def _to_response(ticket: Ticket) -> TicketResponse:
    return TicketResponse(
        id=ticket.id,
        customer_id=ticket.customer_id,
        agent_id=ticket.agent_id,
        priority=ticket.priority,
        status=ticket.status,
        subject=ticket.subject,
        messages=[
            MessageResponse(author=m.author, author_id=m.author_id, body=m.body)
            for m in ticket.messages
        ],
        sla_deadline=ticket.sla_deadline,
        auto_reassign_deadline=ticket.auto_reassign_deadline,
        last_customer_reply_at=ticket.last_customer_reply_at,
        closed_at=ticket.closed_at,
    )


# ------------------------------------------------------------------ #
# Endpoints                                                           #
# ------------------------------------------------------------------ #

@app.get("/tickets", response_model=list[TicketResponse])
def list_tickets() -> list[TicketResponse]:
    return [_to_response(t) for t in _store.values()]


@app.post("/tickets", response_model=TicketResponse, status_code=201)
def open_ticket(body: OpenTicketRequest) -> TicketResponse:
    try:
        ticket = Ticket.open(
            customer_id=body.customer_id,
            priority=body.priority,
            subject=body.subject,
            body=body.body,
        )
    except TicketError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _store[ticket.id] = ticket
    return _to_response(ticket)


@app.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(ticket_id: UUID) -> TicketResponse:
    ticket = _store.get(ticket_id)
    if ticket is None:
        raise _not_found(ticket_id)
    return _to_response(ticket)


@app.post("/tickets/{ticket_id}/messages", response_model=TicketResponse)
def append_message(ticket_id: UUID, body: AppendMessageRequest) -> TicketResponse:
    ticket = _store.get(ticket_id)
    if ticket is None:
        raise _not_found(ticket_id)
    try:
        ticket.append_message(
            author=body.author,
            author_id=body.author_id,
            body=body.body,
        )
    except TicketError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _to_response(ticket)


@app.post("/tickets/{ticket_id}/acknowledge", response_model=TicketResponse)
def acknowledge(ticket_id: UUID, body: AcknowledgeRequest) -> TicketResponse:
    ticket = _store.get(ticket_id)
    if ticket is None:
        raise _not_found(ticket_id)
    ticket.acknowledge(agent_id=body.agent_id)
    return _to_response(ticket)


@app.post("/tickets/{ticket_id}/escalate", response_model=TicketResponse)
def escalate(ticket_id: UUID, body: EscalateRequest) -> TicketResponse:
    ticket = _store.get(ticket_id)
    if ticket is None:
        raise _not_found(ticket_id)
    try:
        ticket.escalate(escalated_by=body.escalated_by)
    except TicketError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _to_response(ticket)


@app.post("/tickets/{ticket_id}/close", response_model=TicketResponse)
def close(ticket_id: UUID, body: CloseRequest) -> TicketResponse:
    ticket = _store.get(ticket_id)
    if ticket is None:
        raise _not_found(ticket_id)
    try:
        ticket.close(closed_by=body.closed_by, closed_by_role=body.closed_by_role)
    except TicketError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _to_response(ticket)


@app.post("/tickets/{ticket_id}/reopen", response_model=TicketResponse)
def reopen(ticket_id: UUID, body: ReopenRequest) -> TicketResponse:
    ticket = _store.get(ticket_id)
    if ticket is None:
        raise _not_found(ticket_id)
    try:
        ticket.reopen(reopened_by=body.reopened_by)
    except TicketError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _to_response(ticket)
