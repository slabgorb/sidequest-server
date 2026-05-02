"""Beat dispatch tests — clock advance via beat kinds."""

from __future__ import annotations

import pytest

from sidequest.orbital.beats import (
    DEFAULT_DURATIONS_HOURS,
    StoryBeat,
    StoryBeatKind,
    advance_clock_via_beat,
)
from sidequest.orbital.clock import Clock


def test_beat_kind_values():
    assert {k.value for k in StoryBeatKind} == {"encounter", "rest", "travel", "downtime"}


def test_default_durations():
    assert DEFAULT_DURATIONS_HOURS[StoryBeatKind.ENCOUNTER] == 1.0
    assert DEFAULT_DURATIONS_HOURS[StoryBeatKind.REST] == 8.0
    # travel and downtime have no static default — duration must be supplied
    assert StoryBeatKind.TRAVEL not in DEFAULT_DURATIONS_HOURS
    assert StoryBeatKind.DOWNTIME not in DEFAULT_DURATIONS_HOURS


def test_encounter_beat_default_advances_one_hour():
    clock = Clock()
    advance_clock_via_beat(clock, StoryBeat(kind=StoryBeatKind.ENCOUNTER, trigger="scene-1"))
    assert clock.t_hours == 1.0


def test_encounter_beat_overridable():
    clock = Clock()
    advance_clock_via_beat(
        clock, StoryBeat(kind=StoryBeatKind.ENCOUNTER, duration_hours=6.0, trigger="negotiation")
    )
    assert clock.t_hours == 6.0


def test_rest_beat_fixed_eight_hours():
    clock = Clock()
    advance_clock_via_beat(clock, StoryBeat(kind=StoryBeatKind.REST, trigger="long-rest"))
    assert clock.t_hours == 8.0


def test_rest_duration_override_rejected():
    """REST is fixed at 8h; passing a different duration is a programming error."""
    clock = Clock()
    with pytest.raises(ValueError, match="REST.*fixed at 8h"):
        advance_clock_via_beat(
            clock, StoryBeat(kind=StoryBeatKind.REST, duration_hours=4.0, trigger="catnap")
        )


def test_travel_beat_requires_duration():
    clock = Clock()
    with pytest.raises(ValueError, match="TRAVEL.*requires.*duration_hours"):
        advance_clock_via_beat(clock, StoryBeat(kind=StoryBeatKind.TRAVEL, trigger="route-x"))


def test_travel_beat_advances_provided_duration():
    clock = Clock()
    advance_clock_via_beat(
        clock, StoryBeat(kind=StoryBeatKind.TRAVEL, duration_hours=432.0, trigger="route-x")
    )
    assert clock.t_hours == 432.0


def test_downtime_requires_duration():
    clock = Clock()
    with pytest.raises(ValueError, match="DOWNTIME.*requires.*duration_hours"):
        advance_clock_via_beat(clock, StoryBeat(kind=StoryBeatKind.DOWNTIME, trigger="wait"))


def test_downtime_advances_provided_duration():
    clock = Clock()
    advance_clock_via_beat(
        clock, StoryBeat(kind=StoryBeatKind.DOWNTIME, duration_hours=72.0, trigger="player-wait")
    )
    assert clock.t_hours == 72.0


def _spans_named(otel_capture, name: str):
    return [s for s in otel_capture.get_finished_spans() if s.name == name]


def test_advance_emits_clock_advance_span(otel_capture):
    """clock.advance span fires with the right attributes on every beat."""
    clock = Clock(t_hours=10.0)
    advance_clock_via_beat(
        clock, StoryBeat(kind=StoryBeatKind.TRAVEL, duration_hours=24.0, trigger="route-xy")
    )

    spans = _spans_named(otel_capture, "clock.advance")
    assert len(spans) == 1
    span = spans[0]
    assert span.attributes["beat_kind"] == "travel"
    assert span.attributes["duration_hours"] == 24.0
    assert span.attributes["t_before_h"] == 10.0
    assert span.attributes["t_after_h"] == 34.0
    assert span.attributes["trigger"] == "route-xy"


def test_advance_emits_for_default_durations(otel_capture):
    clock = Clock()
    advance_clock_via_beat(clock, StoryBeat(kind=StoryBeatKind.ENCOUNTER, trigger="scene-1"))

    spans = _spans_named(otel_capture, "clock.advance")
    assert len(spans) == 1
    assert spans[0].attributes["duration_hours"] == 1.0
    assert spans[0].attributes["beat_kind"] == "encounter"
