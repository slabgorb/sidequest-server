"""SessionRoom.session lifecycle tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.persistence import GameMode, SqliteStore
from sidequest.game.session import GameSnapshot
from sidequest.server.session import Session
from sidequest.server.session_room import SessionRoom


def _make_room(slug: str = "test_world") -> SessionRoom:
    return SessionRoom(slug=slug, mode=GameMode.SOLO)


def test_session_property_raises_before_bind_world():
    room = _make_room()
    with pytest.raises(RuntimeError, match="Session not yet bound"):
        _ = room.session


def test_session_property_returns_session_after_bind_world(tmp_path: Path):
    room = _make_room()
    snap = GameSnapshot()
    store = SqliteStore(tmp_path / "t.db")
    room.bind_world(snapshot=snap, store=store)
    assert isinstance(room.session, Session)
    # Same snapshot reference — Session reads through.
    assert room.session is room.session  # property is stable post-bind
    snap.clock_t_hours = 5.0
    assert room.session.clock.t_hours == 5.0


def test_session_advance_via_room_persists_to_room_snapshot(tmp_path: Path):
    """Advancing via room.session writes to room._snapshot."""
    from sidequest.orbital.beats import StoryBeat, StoryBeatKind

    room = _make_room()
    snap = GameSnapshot()
    store = SqliteStore(tmp_path / "t.db")
    room.bind_world(snapshot=snap, store=store)

    room.session.advance_via_beat(
        StoryBeat(kind=StoryBeatKind.ENCOUNTER, trigger="test")
    )
    # The room's snapshot is the canonical reference.
    assert room.snapshot is snap
    assert snap.clock_t_hours == 1.0


def test_bind_world_is_idempotent_on_session(tmp_path: Path):
    """Second bind_world call (idempotent per existing semantics) does not rebuild Session."""
    room = _make_room()
    snap = GameSnapshot()
    store = SqliteStore(tmp_path / "t.db")
    room.bind_world(snapshot=snap, store=store)
    s1 = room.session
    # Second call is a no-op per existing bind_world idempotency contract.
    room.bind_world(snapshot=snap, store=store)
    s2 = room.session
    assert s1 is s2
