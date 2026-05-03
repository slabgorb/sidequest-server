"""Verify ActionRevealHandler is registered in the WS dispatch registry."""

from sidequest.handlers.action_reveal import ActionRevealHandler
from sidequest.server.session_handler import WebSocketSessionHandler


def test_action_reveal_handler_is_registered() -> None:
    """The dispatch registry must route ACTION_REVEAL to ActionRevealHandler.

    Wiring test — proves the handler is actually reachable from production
    dispatch, not merely importable.
    """
    # Reset the cached registry so this test sees a fresh build.
    WebSocketSessionHandler._MESSAGE_HANDLERS = None  # type: ignore[attr-defined]

    handler = WebSocketSessionHandler._message_handler_for("ACTION_REVEAL")
    assert isinstance(handler, ActionRevealHandler)
