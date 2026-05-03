"""Tests for ActionRevealHandler."""

from unittest.mock import MagicMock

import pytest

from sidequest.handlers.action_reveal import HANDLER, ActionRevealHandler
from sidequest.protocol.messages import (
    ActionRevealMessage,
    ActionRevealPayload,
    ActionRevealStatus,
)


def _make_session(player_id: str = "p1", socket_id: str = "s1", round: int = 7):
    session = MagicMock()
    session._room = MagicMock()
    session._room.broadcast = MagicMock(return_value=[])
    session._room.slug = "test-slug"
    session._socket_id = socket_id
    # Player identity lives on _session_data, not _player_id.
    session._session_data.player_id = player_id
    snapshot = MagicMock()
    snapshot.turn_manager.round = round
    session._room.snapshot.return_value = snapshot
    return session


def _make_msg(
    *,
    status: ActionRevealStatus,
    action: str = "I sneak around the back",
    seq: int = 0,
    round: int = 7,
    player_id: str = "p1",
    character_name: str = "Alex",
    aside: bool = False,
):
    payload = ActionRevealPayload(
        player_id=player_id,
        character_name=character_name,
        status=status,
        action=action,
        aside=aside,
        seq=seq,
        round=round,
    )
    return ActionRevealMessage(payload=payload, player_id=player_id)


@pytest.mark.asyncio
async def test_composing_is_broadcast_excluding_sender() -> None:
    handler = ActionRevealHandler()
    session = _make_session()
    msg = _make_msg(status=ActionRevealStatus.COMPOSING, action="I creep", seq=1)

    result = await handler.handle(session, msg)

    assert result == []
    session._room.broadcast.assert_called_once()
    sent_msg = session._room.broadcast.call_args.args[0]
    kwargs = session._room.broadcast.call_args.kwargs
    assert kwargs["exclude_socket_id"] == "s1"
    assert sent_msg.payload.status == ActionRevealStatus.COMPOSING
    assert sent_msg.payload.action == "I creep"


@pytest.mark.asyncio
async def test_submitted_is_broadcast() -> None:
    handler = ActionRevealHandler()
    session = _make_session()
    msg = _make_msg(status=ActionRevealStatus.SUBMITTED, action="I draw my sword", seq=5)

    await handler.handle(session, msg)

    session._room.broadcast.assert_called_once()
    sent_msg = session._room.broadcast.call_args.args[0]
    assert sent_msg.payload.status == ActionRevealStatus.SUBMITTED


@pytest.mark.asyncio
async def test_server_stamps_round_authoritative() -> None:
    """Client-supplied round is overwritten by snapshot.turn_manager.round."""
    handler = ActionRevealHandler()
    session = _make_session(round=42)
    # Client claims round=99 — server must overwrite to 42
    msg = _make_msg(status=ActionRevealStatus.COMPOSING, round=99, seq=0)

    await handler.handle(session, msg)

    sent_msg = session._room.broadcast.call_args.args[0]
    assert sent_msg.payload.round == 42


@pytest.mark.asyncio
async def test_server_stamps_player_id_authoritative() -> None:
    """Client-supplied player_id in payload is overwritten by session_data.player_id."""
    handler = ActionRevealHandler()
    session = _make_session(player_id="real-player")
    # Client lies about player_id — server overwrites
    msg = _make_msg(
        status=ActionRevealStatus.COMPOSING,
        player_id="fake-player",
        seq=0,
    )

    await handler.handle(session, msg)

    sent_msg = session._room.broadcast.call_args.args[0]
    assert sent_msg.payload.player_id == "real-player"


@pytest.mark.asyncio
async def test_no_session_data_drops_silently() -> None:
    handler = ActionRevealHandler()
    session = _make_session()
    session._session_data = None  # not authenticated yet
    msg = _make_msg(status=ActionRevealStatus.COMPOSING, action="x", seq=0)

    result = await handler.handle(session, msg)

    assert result == []
    session._room.broadcast.assert_not_called()


@pytest.mark.asyncio
async def test_unbound_snapshot_drops_with_warning(caplog) -> None:
    handler = ActionRevealHandler()
    session = _make_session()
    session._room.snapshot.return_value = None  # room not bound yet
    msg = _make_msg(status=ActionRevealStatus.COMPOSING, action="x", seq=0)

    with caplog.at_level("WARNING"):
        result = await handler.handle(session, msg)

    assert result == []
    session._room.broadcast.assert_not_called()
    assert any("not bound" in r.message or "before room bound" in r.message for r in caplog.records)


def test_module_exports_handler_singleton() -> None:
    assert isinstance(HANDLER, ActionRevealHandler)
