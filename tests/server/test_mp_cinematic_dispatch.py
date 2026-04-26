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
