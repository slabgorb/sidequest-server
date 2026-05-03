"""OTEL coverage for ACTION_REVEAL — the GM panel lie-detector.

Per CLAUDE.md observability principle: every subsystem decision must
emit OTEL so the GM panel can verify the feature is engaged.
"""

from unittest.mock import MagicMock, patch

import pytest

from sidequest.handlers.action_reveal import ActionRevealHandler
from sidequest.protocol.messages import (
    ActionRevealMessage,
    ActionRevealPayload,
    ActionRevealStatus,
)


def _session(socket_id: str = "s1", player_id: str = "p1", round_no: int = 7):
    s = MagicMock()
    s._socket_id = socket_id
    s._room.slug = "test-slug"
    s._session_data.player_id = player_id
    snapshot = MagicMock()
    snapshot.turn_manager.round = round_no
    s._room.snapshot.return_value = snapshot
    s._room.broadcast.return_value = []
    return s


def _msg(status, *, action: str = "abc", seq: int = 0, round: int = 7, aside: bool = False):
    return ActionRevealMessage(
        payload=ActionRevealPayload(
            player_id="p1",
            character_name="Alex",
            status=status,
            action=action,
            aside=aside,
            seq=seq,
            round=round,
        ),
        player_id="p1",
    )


@pytest.mark.asyncio
async def test_composing_emits_otel_with_length_only() -> None:
    handler = ActionRevealHandler()
    session = _session()
    with patch("sidequest.handlers.action_reveal._watcher_publish") as pub:
        await handler.handle(session, _msg(ActionRevealStatus.COMPOSING, action="hello world"))
    pub.assert_called_with(
        "action_reveal.composing",
        {
            "slug": "test-slug",
            "player_id": "p1",
            "round": 7,
            "seq": 0,
            "text_length": 11,
        },
        component="multiplayer",
    )


@pytest.mark.asyncio
async def test_submitted_emits_otel_with_aside_flag() -> None:
    handler = ActionRevealHandler()
    session = _session()
    with patch("sidequest.handlers.action_reveal._watcher_publish") as pub:
        await handler.handle(
            session,
            _msg(ActionRevealStatus.SUBMITTED, action="hi there", seq=4, aside=True),
        )
    pub.assert_called_with(
        "action_reveal.submitted",
        {
            "slug": "test-slug",
            "player_id": "p1",
            "round": 7,
            "text_length": 8,
            "aside": True,
        },
        component="multiplayer",
    )


@pytest.mark.asyncio
async def test_rate_limit_emits_dropped_counter(monkeypatch) -> None:
    handler = ActionRevealHandler()
    session = _session()
    fake_now = [1000.0]
    monkeypatch.setattr(
        "sidequest.handlers.action_reveal.time.monotonic",
        lambda: fake_now[0],
    )
    with patch("sidequest.handlers.action_reveal._watcher_publish") as pub:
        await handler.handle(session, _msg(ActionRevealStatus.COMPOSING, seq=0))
        fake_now[0] = 1000.05  # 50ms — under floor
        await handler.handle(session, _msg(ActionRevealStatus.COMPOSING, seq=1))
    names = [c.args[0] for c in pub.call_args_list]
    assert "action_reveal.dropped_rate_limit" in names


@pytest.mark.asyncio
async def test_cleared_event_omits_text_length() -> None:
    """cleared events carry no text — text_length must NOT be in the payload.

    The cleared trigger fires from _broadcast_cleared_to_party (player_action.py)
    and _emit_action_reveal_cleared (session_room.py), not from the handler.
    Verify both call sites emit action_reveal.cleared.
    """
    from sidequest.handlers.player_action import _broadcast_cleared_to_party

    room = MagicMock()
    room.slug = "test-slug"
    room.broadcast.return_value = []

    with patch("sidequest.handlers.player_action._watcher_publish") as pub:
        _broadcast_cleared_to_party(
            room,
            [{"player_id": "p1", "character_name": "Alex"}],
            round_no=7,
            reason="dispatch",
        )

    pub.assert_called_with(
        "action_reveal.cleared",
        {
            "slug": "test-slug",
            "player_id": "p1",
            "round": 7,
            "reason": "dispatch",
        },
        component="multiplayer",
    )


def test_cleared_emitted_from_session_room_disconnect_path() -> None:
    """The session_room helper must also emit action_reveal.cleared."""
    from sidequest.server.session_room import _emit_action_reveal_cleared

    room = MagicMock()
    room.slug = "test-slug"
    room.broadcast.return_value = []

    with patch("sidequest.server.session_room._watcher_publish") as pub:
        _emit_action_reveal_cleared(
            room,
            player_id="p1",
            character_name="Alex",
            round_no=7,
            reason="disconnect",
        )

    pub.assert_called_with(
        "action_reveal.cleared",
        {
            "slug": "test-slug",
            "player_id": "p1",
            "round": 7,
            "reason": "disconnect",
        },
        component="multiplayer",
    )


@pytest.mark.asyncio
async def test_otel_text_length_never_carries_content() -> None:
    """Defense-in-depth: scan emitted payloads for any 'action' or 'text' key
    that contains the actual content. Only text_length is permitted."""
    handler = ActionRevealHandler()
    session = _session()
    secret = "the secret plan is to attack at dawn"
    with patch("sidequest.handlers.action_reveal._watcher_publish") as pub:
        await handler.handle(
            session,
            _msg(ActionRevealStatus.COMPOSING, action=secret),
        )
    for call in pub.call_args_list:
        payload = call.args[1]
        for key, value in payload.items():
            if isinstance(value, str):
                assert secret not in value, f"OTEL leaked content via {key!r}"
            assert key != "action", "OTEL must not carry the action text"
            assert key != "text", "OTEL must not carry text content"
