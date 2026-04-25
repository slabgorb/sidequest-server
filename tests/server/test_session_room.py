from unittest.mock import MagicMock

import pytest

from sidequest.game.persistence import GameMode
from sidequest.game.session import GameSnapshot
from sidequest.server.session_room import RoomRegistry, SessionRoom, SoloSlotConflict


def test_room_registry_returns_same_room_for_same_slug():
    reg = RoomRegistry()
    r1 = reg.get_or_create("slug-a", mode=GameMode.MULTIPLAYER)
    r2 = reg.get_or_create("slug-a", mode=GameMode.MULTIPLAYER)
    assert r1 is r2


def test_room_tracks_connected_players():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.connect("alice", socket_id="sock-1")
    room.connect("bob", socket_id="sock-2")
    assert set(room.connected_player_ids()) == {"alice", "bob"}


def test_room_disconnect_removes_player():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.connect("alice", socket_id="sock-1")
    room.disconnect(socket_id="sock-1")
    assert room.connected_player_ids() == []


def test_same_player_reconnect_updates_socket():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.connect("alice", socket_id="sock-1")
    room.connect("alice", socket_id="sock-2")
    room.disconnect(socket_id="sock-1")
    assert "alice" in room.connected_player_ids()  # sock-2 still holds alice


def test_solo_room_rejects_second_connection():
    room = SessionRoom(slug="slug-a", mode=GameMode.SOLO)
    room.connect("alice", socket_id="sock-1")
    with pytest.raises(SoloSlotConflict):
        room.connect("bob", socket_id="sock-2")


def test_solo_room_allows_same_player_reconnect():
    room = SessionRoom(slug="slug-a", mode=GameMode.SOLO)
    room.connect("alice", socket_id="sock-1")
    room.disconnect(socket_id="sock-1")
    room.connect("alice", socket_id="sock-2")  # must not raise
    assert room.connected_player_ids() == ["alice"]


def test_seated_players_separate_from_connected():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.seat("alice", character_slot="rux")
    room.seat("bob", character_slot="vex")
    room.connect("alice", socket_id="sock-1")
    # bob seated but not connected
    assert set(room.seated_player_ids()) == {"alice", "bob"}
    assert set(room.connected_player_ids()) == {"alice"}
    assert set(room.absent_seated_player_ids()) == {"bob"}


def test_slot_to_player_id_returns_seat_map():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.seat("alice", character_slot="Laverne")
    room.seat("bob", character_slot="Shirley")
    assert room.slot_to_player_id() == {"Laverne": "alice", "Shirley": "bob"}


def test_slot_to_player_id_skips_seats_without_slot():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.seat("alice", character_slot=None)  # legacy / pre-slot seat
    room.seat("bob", character_slot="Shirley")
    assert room.slot_to_player_id() == {"Shirley": "bob"}


def test_slot_to_player_id_empty_when_no_seats():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    assert room.slot_to_player_id() == {}


# ---------------------------------------------------------------------------
# Canonical snapshot binding (ADR-037 Python port)
# ---------------------------------------------------------------------------


def _fresh_snapshot() -> GameSnapshot:
    return GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        location="Entrance",
    )


def test_bind_world_sets_snapshot_and_store_once():
    """First bind populates both fields; getters reflect them."""
    room = SessionRoom(slug="2026-04-25-test-mp", mode=GameMode.MULTIPLAYER)
    snap = _fresh_snapshot()
    store = MagicMock()

    assert room.snapshot is None
    assert room.store is None

    room.bind_world(snapshot=snap, store=store)

    assert room.snapshot is snap
    assert room.store is store


def test_bind_world_is_idempotent():
    """Second bind when already populated is a no-op (no overwrite, no raise).

    Guards against a race where two concurrent first-connects both try to
    bind. The first wins; the second silently observes the existing
    binding rather than stomping it.
    """
    room = SessionRoom(slug="slug", mode=GameMode.MULTIPLAYER)
    snap1 = _fresh_snapshot()
    store1 = MagicMock()
    snap2 = _fresh_snapshot()
    store2 = MagicMock()

    room.bind_world(snapshot=snap1, store=store1)
    room.bind_world(snapshot=snap2, store=store2)

    assert room.snapshot is snap1
    assert room.store is store1


def test_close_store_is_idempotent_and_calls_close_once():
    """close_store closes the bound store exactly once across N calls."""
    room = SessionRoom(slug="slug", mode=GameMode.MULTIPLAYER)
    store = MagicMock()
    room.bind_world(snapshot=_fresh_snapshot(), store=store)

    room.close_store()
    room.close_store()

    assert store.close.call_count == 1


def test_close_store_when_unbound_is_noop():
    """Pre-bind / never-bound rooms must not raise on close."""
    room = SessionRoom(slug="slug", mode=GameMode.MULTIPLAYER)
    room.close_store()  # must not raise
