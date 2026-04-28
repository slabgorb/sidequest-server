"""First-class WebSocket message handlers.

Each module in this package owns the logic for one inbound message type.
Handlers implement the :class:`MessageHandler` protocol from
:mod:`sidequest.handlers.base` — a single ``async def handle(session, msg)``
method that the session dispatcher fans messages out to.

Handlers are stateless singletons; each module exports a module-level
``HANDLER`` instance that the session registry references.
"""
