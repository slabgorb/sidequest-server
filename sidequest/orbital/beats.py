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


class BeatKind(Enum):
    """The four beat kinds. The clock advances only via these."""

    ENCOUNTER = "encounter"
    REST = "rest"
    TRAVEL = "travel"
    DOWNTIME = "downtime"


# Beats with a static default duration. Encounter defaults to 1h but may be
# overridden by the narrator per scene. Rest is fixed (override rejected).
# Travel and Downtime have no default — duration is always supplied.
DEFAULT_DURATIONS_HOURS: dict[BeatKind, float] = {
    BeatKind.ENCOUNTER: 1.0,
    BeatKind.REST: 8.0,
}


@dataclass(frozen=True)
class Beat:
    """One clock-advance event.

    `trigger` is a free-form string identifying the cause (scene id, route
    id, player action id) — captured in the OTEL span for traceability.
    `duration_hours=None` means "use the default for this kind"; required
    for kinds without a default.
    """

    kind: BeatKind
    trigger: str
    duration_hours: float | None = None


def advance_clock_via_beat(clock: Clock, beat: Beat) -> float:
    """Advance the clock per the beat's kind and duration.

    Returns the actual hours advanced (handy for callers that want to log
    or surface it). Raises `ValueError` if the beat is malformed for its
    kind (e.g. REST with a non-default duration; TRAVEL without duration).
    """
    duration = _resolve_duration(beat)
    clock.advance(duration)
    return duration


def _resolve_duration(beat: Beat) -> float:
    if beat.kind == BeatKind.REST:
        if beat.duration_hours is not None and beat.duration_hours != 8.0:
            raise ValueError(
                f"REST beat duration is fixed at 8h; got {beat.duration_hours!r} "
                f"(trigger={beat.trigger!r})"
            )
        return 8.0
    if beat.kind == BeatKind.ENCOUNTER:
        return beat.duration_hours if beat.duration_hours is not None else 1.0
    # TRAVEL and DOWNTIME require explicit duration
    if beat.duration_hours is None:
        raise ValueError(
            f"{beat.kind.name} beat requires explicit duration_hours "
            f"(trigger={beat.trigger!r})"
        )
    return beat.duration_hours
