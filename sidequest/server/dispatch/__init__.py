"""Message dispatch package.

Mirrors ``sidequest-server/src/dispatch/`` in the Rust port — a package of
submodules keyed on message type and lifecycle concern
(``chargen_summary``, upcoming ``persistence``, ``opening_turn``, etc.).

This file is intentionally empty of re-exports. Importing
``WebSocketSessionHandler`` from here would form a cycle with
``sidequest.server.session_handler`` (which imports the chargen_summary
submodule). Callers that need the handler import it directly from
``sidequest.server.session_handler``.
"""
