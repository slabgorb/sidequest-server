"""Fixtures for integration tests.

Re-exports ``session_fixture`` from ``tests.server.conftest`` so integration
tests can build a real ``WebSocketSessionHandler`` + ``_SessionData`` without
re-implementing the fixture.
"""
from __future__ import annotations

from tests.server.conftest import session_fixture  # noqa: F401
