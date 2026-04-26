"""Wiring tests for ADR-036 Cinematic mode — see
docs/superpowers/specs/2026-04-26-mp-cinematic-mode-wiring-design.md.

These tests verify the multiplayer barrier + dispatch election. Each test
either calls SessionRoom helpers directly (unit) or drives
``_handle_player_action`` end-to-end with mocked Claude (integration).
"""
from __future__ import annotations

import asyncio
import pytest

from sidequest.game.persistence import GameMode
from sidequest.server.session_room import PendingAction, SessionRoom


def test_pending_action_dataclass_holds_character_and_action() -> None:
    pa = PendingAction(character_name="Gladstone", action="I prepare for the dungeon")
    assert pa.character_name == "Gladstone"
    assert pa.action == "I prepare for the dungeon"


def test_record_and_drain_returns_in_submission_order() -> None:
    room = SessionRoom(slug="test-slug", mode=GameMode.MULTIPLAYER)
    room.record_pending_action("p1", "Gladstone", "I prepare for the dungeon")
    room.record_pending_action("p2", "Zanzibar Jones", "I get my pole")
    drained = room.drain_pending_actions()
    assert [pid for pid, _ in drained] == ["p1", "p2"]
    assert drained[0][1].character_name == "Gladstone"
    assert drained[0][1].action == "I prepare for the dungeon"
    assert drained[1][1].character_name == "Zanzibar Jones"
    assert drained[1][1].action == "I get my pole"


def test_drain_empties_the_buffer() -> None:
    room = SessionRoom(slug="test-slug", mode=GameMode.MULTIPLAYER)
    room.record_pending_action("p1", "Glad", "act1")
    room.drain_pending_actions()
    assert room.drain_pending_actions() == []


def test_record_same_player_twice_is_last_write_wins() -> None:
    room = SessionRoom(slug="test-slug", mode=GameMode.MULTIPLAYER)
    room.record_pending_action("p1", "Gladstone", "I changed my mind")
    room.record_pending_action("p1", "Gladstone", "I really changed my mind")
    drained = room.drain_pending_actions()
    assert len(drained) == 1
    assert drained[0][1].action == "I really changed my mind"


def test_dispatch_lock_is_an_asyncio_lock() -> None:
    room = SessionRoom(slug="test-slug", mode=GameMode.MULTIPLAYER)
    assert isinstance(room.dispatch_lock, asyncio.Lock)


def test_last_dispatched_round_starts_at_zero() -> None:
    room = SessionRoom(slug="test-slug", mode=GameMode.MULTIPLAYER)
    assert room.last_dispatched_round == 0


def test_last_dispatched_round_is_writable() -> None:
    room = SessionRoom(slug="test-slug", mode=GameMode.MULTIPLAYER)
    room.last_dispatched_round = 5
    assert room.last_dispatched_round == 5


def test_seated_player_count_returns_zero_when_no_seats() -> None:
    room = SessionRoom(slug="test-slug", mode=GameMode.MULTIPLAYER)
    assert room.seated_player_count() == 0


def test_seated_player_count_after_seat() -> None:
    room = SessionRoom(slug="test-slug", mode=GameMode.MULTIPLAYER)
    room.connect("p1", socket_id="s1")
    room.seat("p1", character_slot="Gladstone")
    room.connect("p2", socket_id="s2")
    room.seat("p2", character_slot="Zanzibar Jones")
    assert room.seated_player_count() == 2


# ---------------------------------------------------------------------------
# ADR-036 Task 3 — buffer+barrier wiring
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from sidequest.protocol.messages import PlayerActionMessage, PlayerActionPayload  # noqa: E402
from sidequest.protocol.types import NonBlankString  # noqa: E402


@pytest.mark.asyncio
async def test_first_of_two_players_buffers_and_returns_empty(
    session_handler_factory,
) -> None:
    """When player 1 submits in a 2-seat room, the action is buffered and
    the handler returns [] (still waiting on player 2). The narrator must
    NOT run yet."""
    handler, sd, room = session_handler_factory(
        slug="test-mp-grimvault",
        mode=GameMode.MULTIPLAYER,
        seat_players=[("p1", "Gladstone"), ("p2", "Zanzibar Jones")],
        active_player=("p1", "Gladstone"),
    )
    # Spy on _execute_narration_turn — it must NOT be called this turn.
    handler._execute_narration_turn = AsyncMock(  # type: ignore[method-assign]
        return_value=[],
    )

    msg = PlayerActionMessage(
        payload=PlayerActionPayload(
            action=NonBlankString.model_validate("I prepare for the dungeon"),
        ),
        player_id="p1",
    )
    result = await handler._handle_player_action(msg)

    assert result == []
    handler._execute_narration_turn.assert_not_called()
    # Buffer holds Gladstone's action.
    drained = room.drain_pending_actions()
    assert len(drained) == 1
    assert drained[0][0] == "p1"
    assert drained[0][1].character_name == "Gladstone"
    assert drained[0][1].action == "I prepare for the dungeon"
