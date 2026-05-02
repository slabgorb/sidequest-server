"""Session aggregate unit tests.

Session is constructed directly over a GameSnapshot for these unit tests.
SessionRoom binding is covered separately in test_session_room_session_binding.
"""

from __future__ import annotations

import pytest

from sidequest.game.session import GameSnapshot
from sidequest.orbital.beats import StoryBeat, StoryBeatKind
from sidequest.orbital.clock import Clock
from sidequest.server.session import Session


def test_session_construction_starts_at_snapshot_t_hours_zero():
    snap = GameSnapshot()
    session = Session(snap)
    assert session.clock.t_hours == 0.0


def test_session_construction_honors_existing_t_hours():
    snap = GameSnapshot(clock_t_hours=72.0)
    session = Session(snap)
    assert session.clock.t_hours == 72.0


def test_session_clock_property_returns_clock_instance():
    snap = GameSnapshot(clock_t_hours=10.0)
    session = Session(snap)
    clk = session.clock
    assert isinstance(clk, Clock)
    assert clk.t_hours == 10.0


def test_session_clock_view_is_read_only():
    """Mutations on the returned Clock do NOT persist."""
    snap = GameSnapshot(clock_t_hours=5.0)
    session = Session(snap)
    clk = session.clock
    clk.advance(99.0)
    # Reading again gives original value — the prior Clock was a throwaway.
    assert session.clock.t_hours == 5.0
    assert snap.clock_t_hours == 5.0


def test_session_advance_via_beat_persists_to_snapshot():
    snap = GameSnapshot()
    session = Session(snap)
    session.advance_via_beat(StoryBeat(kind=StoryBeatKind.ENCOUNTER, trigger="test"))
    assert snap.clock_t_hours == 1.0
    assert session.clock.t_hours == 1.0


def test_session_advance_via_beat_returns_duration():
    snap = GameSnapshot()
    session = Session(snap)
    duration = session.advance_via_beat(
        StoryBeat(kind=StoryBeatKind.TRAVEL, duration_hours=24.0, trigger="route-x")
    )
    assert duration == 24.0
    assert snap.clock_t_hours == 24.0


def test_session_advance_via_beat_emits_clock_advance_span(otel_capture):
    snap = GameSnapshot(clock_t_hours=10.0)
    session = Session(snap)
    session.advance_via_beat(
        StoryBeat(kind=StoryBeatKind.TRAVEL, duration_hours=24.0, trigger="route-x")
    )
    spans = [s for s in otel_capture.get_finished_spans() if s.name == "clock.advance"]
    assert len(spans) == 1
    assert spans[0].attributes["beat_kind"] == "travel"
    assert spans[0].attributes["t_before_h"] == 10.0
    assert spans[0].attributes["t_after_h"] == 34.0
    assert spans[0].attributes["trigger"] == "route-x"


def test_session_advance_via_beat_propagates_validation_errors():
    """Malformed beats raise ValueError from advance_clock_via_beat."""
    snap = GameSnapshot()
    session = Session(snap)
    with pytest.raises(ValueError, match="REST.*fixed at 8h"):
        session.advance_via_beat(
            StoryBeat(kind=StoryBeatKind.REST, duration_hours=4.0, trigger="catnap")
        )


def test_session_end_scene_advances_clock_by_one_hour(otel_capture):
    snap = GameSnapshot()
    session = Session(snap)
    session.end_scene("scene_end", turn=1)
    assert snap.clock_t_hours == 1.0
    spans = [s for s in otel_capture.get_finished_spans() if s.name == "clock.advance"]
    assert len(spans) == 1
    assert spans[0].attributes["beat_kind"] == "encounter"
    assert spans[0].attributes["trigger"] == "scene-scene_end"
