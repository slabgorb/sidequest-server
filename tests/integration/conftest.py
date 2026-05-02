"""Fixtures for integration tests.

Re-exports fixtures from ``tests.server.conftest`` so integration tests can
build a real ``WebSocketSessionHandler`` + ``_SessionData`` and drive
encounter engine paths without re-implementing the fixtures.
"""

from __future__ import annotations

from tests.server.conftest import (  # noqa: F401
    encounter_dispatch_helper,
    otel_capture,
    session_fixture,
    session_handler_factory,
    store_bound_to_hub,
    synthetic_two_dial_pack,
)
