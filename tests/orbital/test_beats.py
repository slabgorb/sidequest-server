"""Beat dispatch tests — clock advance via beat kinds."""
from __future__ import annotations

import pytest

from sidequest.orbital.beats import (
    Beat,
    BeatKind,
    DEFAULT_DURATIONS_HOURS,
    advance_clock_via_beat,
)
from sidequest.orbital.clock import Clock


def test_beat_kind_values():
    assert {k.value for k in BeatKind} == {"encounter", "rest", "travel", "downtime"}


def test_default_durations():
    assert DEFAULT_DURATIONS_HOURS[BeatKind.ENCOUNTER] == 1.0
    assert DEFAULT_DURATIONS_HOURS[BeatKind.REST] == 8.0
    # travel and downtime have no static default — duration must be supplied
    assert BeatKind.TRAVEL not in DEFAULT_DURATIONS_HOURS
    assert BeatKind.DOWNTIME not in DEFAULT_DURATIONS_HOURS


def test_encounter_beat_default_advances_one_hour():
    clock = Clock()
    advance_clock_via_beat(clock, Beat(kind=BeatKind.ENCOUNTER, trigger="scene-1"))
    assert clock.t_hours == 1.0


def test_encounter_beat_overridable():
    clock = Clock()
    advance_clock_via_beat(
        clock, Beat(kind=BeatKind.ENCOUNTER, duration_hours=6.0, trigger="negotiation")
    )
    assert clock.t_hours == 6.0


def test_rest_beat_fixed_eight_hours():
    clock = Clock()
    advance_clock_via_beat(clock, Beat(kind=BeatKind.REST, trigger="long-rest"))
    assert clock.t_hours == 8.0


def test_rest_duration_override_rejected():
    """REST is fixed at 8h; passing a different duration is a programming error."""
    clock = Clock()
    with pytest.raises(ValueError, match="REST.*fixed at 8h"):
        advance_clock_via_beat(
            clock, Beat(kind=BeatKind.REST, duration_hours=4.0, trigger="catnap")
        )


def test_travel_beat_requires_duration():
    clock = Clock()
    with pytest.raises(ValueError, match="TRAVEL.*requires.*duration_hours"):
        advance_clock_via_beat(clock, Beat(kind=BeatKind.TRAVEL, trigger="route-x"))


def test_travel_beat_advances_provided_duration():
    clock = Clock()
    advance_clock_via_beat(
        clock, Beat(kind=BeatKind.TRAVEL, duration_hours=432.0, trigger="route-x")
    )
    assert clock.t_hours == 432.0


def test_downtime_requires_duration():
    clock = Clock()
    with pytest.raises(ValueError, match="DOWNTIME.*requires.*duration_hours"):
        advance_clock_via_beat(clock, Beat(kind=BeatKind.DOWNTIME, trigger="wait"))


def test_downtime_advances_provided_duration():
    clock = Clock()
    advance_clock_via_beat(
        clock, Beat(kind=BeatKind.DOWNTIME, duration_hours=72.0, trigger="player-wait")
    )
    assert clock.t_hours == 72.0
