import pytest
from sidequest.server.session_room import SessionRoom, RoomRegistry, SoloSlotConflict
from sidequest.game.persistence import GameMode


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
