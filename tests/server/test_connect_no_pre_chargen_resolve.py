"""Verify connect.py no longer calls the old resolve_opening at connect time."""

from __future__ import annotations

import inspect

from sidequest.handlers import connect


def test_connect_does_not_import_resolve_opening() -> None:
    """The old resolve_opening from opening_hook.py is dead.
    connect.py should not import it; opening resolution moved to
    chargen-completion in websocket_session_handler.
    """
    source = inspect.getsource(connect)
    assert "from sidequest.server.dispatch.opening import resolve_opening" not in source
    assert "from sidequest.server.dispatch.opening_hook import" not in source


def test_connect_no_longer_calls_resolve_opening() -> None:
    source = inspect.getsource(connect)
    assert "resolve_opening(" not in source
