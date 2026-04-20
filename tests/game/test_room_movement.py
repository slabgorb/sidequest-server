"""Tests for ``sidequest.game.room_movement`` — Story 2.3 Slice E.

Exercises :func:`init_room_graph_location` only: entrance discovery,
first-wins on duplicate entrances, missing-entrance error, idempotency.
"""

from __future__ import annotations

import pytest

from sidequest.game.room_movement import (
    RoomGraphInitError,
    init_room_graph_location,
)
from sidequest.game.session import GameSnapshot
from sidequest.genre.models.world import RoomDef


def _room(id_: str, room_type: str, name: str | None = None) -> RoomDef:
    return RoomDef(id=id_, name=name or id_.title(), room_type=room_type)


class TestInitRoomGraphLocation:
    def test_picks_entrance_and_updates_snapshot(self) -> None:
        snap = GameSnapshot()
        rooms = [
            _room("foyer", "normal"),
            _room("threshold", "entrance"),
            _room("vault", "treasure"),
        ]
        entrance_id = init_room_graph_location(snap, rooms)

        assert entrance_id == "threshold"
        assert snap.location == "threshold"
        assert snap.discovered_rooms == ["threshold"]

    def test_first_entrance_wins_on_duplicates(self) -> None:
        snap = GameSnapshot()
        rooms = [
            _room("alpha", "entrance"),
            _room("beta", "entrance"),
        ]
        entrance_id = init_room_graph_location(snap, rooms)
        assert entrance_id == "alpha"
        assert snap.location == "alpha"

    def test_missing_entrance_raises(self) -> None:
        snap = GameSnapshot()
        rooms = [_room("a", "normal"), _room("b", "treasure")]
        with pytest.raises(RoomGraphInitError) as exc_info:
            init_room_graph_location(snap, rooms)
        assert "2 rooms checked" in str(exc_info.value)

    def test_empty_rooms_raises(self) -> None:
        snap = GameSnapshot()
        with pytest.raises(RoomGraphInitError) as exc_info:
            init_room_graph_location(snap, [])
        assert "0 rooms checked" in str(exc_info.value)

    def test_idempotent_does_not_duplicate_discovered(self) -> None:
        snap = GameSnapshot()
        rooms = [_room("threshold", "entrance")]
        init_room_graph_location(snap, rooms)
        init_room_graph_location(snap, rooms)
        assert snap.discovered_rooms == ["threshold"]

    def test_preserves_existing_discovered_rooms(self) -> None:
        snap = GameSnapshot(discovered_rooms=["cache", "sealed_chamber"])
        rooms = [_room("threshold", "entrance")]
        init_room_graph_location(snap, rooms)
        assert snap.discovered_rooms == ["cache", "sealed_chamber", "threshold"]

    def test_snapshot_location_is_id_not_name(self) -> None:
        """The Rust call site reads ``snap.location`` as a canonical room ID
        downstream. Using the display name here would silently break every
        subsequent room-movement validation call."""
        snap = GameSnapshot()
        rooms = [_room("threshold", "entrance", name="The Threshold")]
        init_room_graph_location(snap, rooms)
        assert snap.location == "threshold"
