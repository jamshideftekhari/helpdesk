Helpdesk
========

A help desk domain model built with Domain-Driven Design.
All business rules live in the Ticket aggregate. No framework, no database.


Project layout
--------------

  ticket/domain/aggregates.py     Ticket aggregate — all business rules
  ticket/domain/value_objects.py  Priority, TicketStatus, SLAPolicy, Message
  ticket/domain/events.py         Domain events raised by the aggregate
  shared/base_aggregate.py        AggregateRoot base class and DomainEvent marker
  api/main.py                     FastAPI HTTP API
  api/schemas.py                  Pydantic request / response models
  tests/test_ticket_aggregate.py  Unit tests (pure, no I/O)
  demo.py                         Interactive command-line demo


Requirements
------------

  Python 3.10+

  Domain layer only (no extra packages needed):
    python demo.py
    python -m pytest tests/

  API:
    pip install fastapi uvicorn


Running the demo
----------------

  python demo.py

  The demo runs a simulated clock so you can fast-forward through SLA windows,
  escalation, and closure scenarios without waiting.

  Type  help  at the prompt to see all commands.
  Type  scenario basic  for a guided walkthrough.


Running the API
---------------

  From the project root:

    python -m uvicorn api.main:app --reload

  Interactive docs are then available at:

    http://localhost:8000/docs

  Endpoints:

    POST   /tickets                     Open a new ticket
    GET    /tickets/{id}                Get ticket state
    POST   /tickets/{id}/messages       Append a message (customer or agent)
    POST   /tickets/{id}/acknowledge    Agent acknowledges an escalated ticket
    POST   /tickets/{id}/escalate       Customer escalates after SLA breach
    POST   /tickets/{id}/close          Close a ticket
    POST   /tickets/{id}/reopen         Customer reopens a recently closed ticket

  Note: the store is in-memory. Data is lost when the server restarts.


Deploying to Azure App Service
-------------------------------

  1. Create a Web App in the Azure portal:
       Publish: Code  |  Runtime: Python 3.12  |  OS: Linux

  2. Set the startup command:
       Configuration -> Stack settings -> Startup Command
         python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

  3. Deploy from VS Code:
       Azure panel -> App Services -> right-click your app -> Deploy to Web App...
       Select the helpdesk folder.

  The interactive docs will then be available at:
    https://<your-app-name>.azurewebsites.net/docs


Running the tests
-----------------

  From the project root:

    python -m pytest tests/


SLA windows (by priority)
--------------------------

  urgent    1 hour
  high      4 hours
  medium    8 hours
  low       24 hours

  Escalation reduces the remaining response window by 33%.
  If the assigned agent does not acknowledge within 50% of the reduced window,
  the ticket is auto-reassigned.

  A ticket is auto-closed if the customer does not reply within 7 days.
  A closed ticket can be reopened within 7 days of closure.
