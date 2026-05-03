"""Find the next conjunction event for the chart HUD.

Spec: docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md §9.

A "conjunction" here = a local minimum of angular separation between two
bodies as seen from their common ancestor (typically the system's star).
The chart's bottom HUD strip shows the soonest such event across all
configured pairs, with a live countdown that ticks toward it.

Algorithm:
  1. Coarse 1-day grid scan over `horizon_days` ahead of `t_hours`,
     computing angular separation at each grid point.
  2. First local minimum (curr < prev AND curr < next) brackets the event.
  3. Golden-section refinement narrows the bracket to ±0.1 hour.

Per-pair search is independent; the soonest event across all pairs wins.
Returns None if no pair has an event within the horizon.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sidequest.orbital.models import (
    BodyDef,
    ConjunctionPair,
    OrbitsConfig,
)
from sidequest.orbital.position import kepler_position

_GRID_STEP_HOURS = 24.0
_REFINE_TOL_HOURS = 0.1
_GOLDEN_RATIO = (math.sqrt(5.0) - 1.0) / 2.0  # ≈ 0.618

# A grid-bracket counts as a real local minimum only if the center sample
# is at least this many degrees below BOTH neighbors. Without it, two bodies
# with constant separation produce false-positive minima from
# floating-point noise in the Kepler solver.
_MIN_SIGNIFICANCE_DEG = 0.5


@dataclass(frozen=True)
class ConjunctionEvent:
    """A scheduled minimum-separation event for a watched pair."""

    body_a_id: str
    body_b_id: str
    label: str
    t_hours_event: float
    t_hours_until: float
    min_separation_deg: float


def next_conjunction(
    orbits: OrbitsConfig,
    t_hours: float,
    horizon_days: int = 365,
) -> ConjunctionEvent | None:
    """Soonest conjunction event across all watched pairs within `horizon_days`.

    Returns None if no pairs are configured or no minimum lands inside the
    horizon. Caller can use None as the signal to hide the HUD's bottom-left
    countdown panel.
    """
    if not orbits.conjunctions:
        return None

    horizon_hours = horizon_days * 24.0
    soonest: ConjunctionEvent | None = None

    for pair in orbits.conjunctions:
        event = _first_local_min_for_pair(orbits, pair, t_hours, horizon_hours)
        if event is None:
            continue
        if soonest is None or event.t_hours_until < soonest.t_hours_until:
            soonest = event

    return soonest


def _first_local_min_for_pair(
    orbits: OrbitsConfig,
    pair: ConjunctionPair,
    t_hours_now: float,
    horizon_hours: float,
) -> ConjunctionEvent | None:
    """Coarse scan + golden-section refinement for one pair."""
    body_a = orbits.bodies[pair.body_a]
    body_b = orbits.bodies[pair.body_b]
    label = pair.label or _default_pair_label(pair, body_a, body_b)

    def sep_at(t: float) -> float:
        return _angular_separation_deg(body_a, body_b, t)

    # Coarse 1-day grid scan: collect (t, sep) at integer-day offsets,
    # then scan triples for a local minimum.
    n_steps = int(horizon_hours / _GRID_STEP_HOURS) + 1
    if n_steps < 3:
        return None  # Horizon too short to bracket anything.

    sep_prev = sep_at(t_hours_now)
    sep_curr = sep_at(t_hours_now + _GRID_STEP_HOURS)
    for i in range(2, n_steps):
        t_next_grid = t_hours_now + i * _GRID_STEP_HOURS
        sep_next = sep_at(t_next_grid)

        if (
            sep_curr < sep_prev - _MIN_SIGNIFICANCE_DEG
            and sep_curr < sep_next - _MIN_SIGNIFICANCE_DEG
        ):
            # Bracket: minimum is between (i-2)*step and i*step.
            t_lo = t_hours_now + (i - 2) * _GRID_STEP_HOURS
            t_hi = t_next_grid
            t_event = _golden_section_min(sep_at, t_lo, t_hi)
            return ConjunctionEvent(
                body_a_id=pair.body_a,
                body_b_id=pair.body_b,
                label=label,
                t_hours_event=t_event,
                t_hours_until=t_event - t_hours_now,
                min_separation_deg=sep_at(t_event),
            )

        sep_prev, sep_curr = sep_curr, sep_next

    return None


def _angular_separation_deg(body_a: BodyDef, body_b: BodyDef, t_hours: float) -> float:
    """Angular separation between two co-orbital bodies at time t.

    Returns a value in [0, 180]. 0° = aligned same side of the common
    ancestor (true conjunction). 180° = opposition. Both extremes are
    "alignment events" in the popular sense; the renderer treats either
    as worth surfacing.
    """
    _, theta_a = kepler_position(body_a, t_hours)
    _, theta_b = kepler_position(body_b, t_hours)
    diff = abs(theta_a - theta_b) % 360.0
    return min(diff, 360.0 - diff)


def _golden_section_min(
    f,
    a: float,
    b: float,
) -> float:
    """Golden-section search for the minimum of `f` on [a, b].

    Assumes f is unimodal on the bracket (the coarse scan guarantees this
    locally). Returns the t-value of the minimum to within _REFINE_TOL_HOURS.
    """
    invphi = _GOLDEN_RATIO  # 1/φ
    invphi2 = invphi * invphi  # 1/φ²

    h = b - a
    c = a + invphi2 * h
    d = a + invphi * h
    fc = f(c)
    fd = f(d)

    while abs(b - a) > _REFINE_TOL_HOURS:
        if fc < fd:
            b, d, fd = d, c, fc
            h = b - a
            c = a + invphi2 * h
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            h = b - a
            d = a + invphi * h
            fd = f(d)

    return (a + b) / 2.0


def _default_pair_label(pair: ConjunctionPair, body_a: BodyDef, body_b: BodyDef) -> str:
    """Default display label when chart.yaml didn't override."""
    a_label = body_a.label or pair.body_a.upper()
    b_label = body_b.label or pair.body_b.upper()
    return f"{a_label} ↔ {b_label}"
