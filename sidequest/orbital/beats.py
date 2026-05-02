"""Beat taxonomy and clock-advance dispatch.

Per spec §3.2: four beat kinds. Encounter has a 1h default that the narrator
can override. Rest is fixed at 8h. Travel duration is computed by Track C
and supplied to this module as a parameter. Downtime is player-declared.

Every beat advance emits a `clock.advance` OTEL span — see Task 3 for that
wiring. The dispatcher itself does not yet emit; it just mutates the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sidequest.orbital.clock import Clock
from sidequest.telemetry.spans.clock import emit_clock_advance


class StoryBeatKind(Enum):
    """The four beat kinds. The clock advances only via these."""

    ENCOUNTER = "encounter"
    REST = "rest"
    TRAVEL = "travel"
    DOWNTIME = "downtime"


# Beats with a static default duration. Encounter defaults to 1h but may be
# overridden by the narrator per scene. Rest is fixed (override rejected).
# Travel and Downtime have no default — duration is always supplied.
DEFAULT_DURATIONS_HOURS: dict[StoryBeatKind, float] = {
    StoryBeatKind.ENCOUNTER: 1.0,
    StoryBeatKind.REST: 8.0,
}


@dataclass(frozen=True)
class StoryBeat:
    """One clock-advance event.

    `trigger` is a free-form string identifying the cause (scene id, route
    id, player action id) — captured in the OTEL span for traceability.
    `duration_hours=None` means "use the default for this kind"; required
    for kinds without a default.
    """

    kind: StoryBeatKind
    trigger: str
    duration_hours: float | None = None


def advance_clock_via_beat(clock: Clock, beat: StoryBeat) -> float:
    """Advance the clock per the beat's kind and duration.

    Returns the actual hours advanced (handy for callers that want to log
    or surface it). Raises `ValueError` if the beat is malformed for its
    kind (e.g. REST with a non-default duration; TRAVEL without duration).
    """
    duration = _resolve_duration(beat)
    t_before = clock.t_hours
    clock.advance(duration)
    emit_clock_advance(
        beat_kind=beat.kind.value,
        duration_hours=duration,
        t_before_h=t_before,
        t_after_h=clock.t_hours,
        trigger=beat.trigger,
    )
    return duration


def _resolve_duration(beat: StoryBeat) -> float:
    if beat.kind == StoryBeatKind.REST:
        if beat.duration_hours is not None and beat.duration_hours != 8.0:
            raise ValueError(
                f"REST beat duration is fixed at 8h; got {beat.duration_hours!r} "
                f"(trigger={beat.trigger!r})"
            )
        return 8.0
    if beat.kind == StoryBeatKind.ENCOUNTER:
        return beat.duration_hours if beat.duration_hours is not None else 1.0
    # TRAVEL and DOWNTIME require explicit duration
    if beat.duration_hours is None:
        raise ValueError(
            f"{beat.kind.name} beat requires explicit duration_hours (trigger={beat.trigger!r})"
        )
    return beat.duration_hours
