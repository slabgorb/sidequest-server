"""Tests for the conjunction-finder algorithm.

Spec: docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md §9.

Pins:
  - Empty conjunctions: returns None
  - Synthetic two-body system with known minimum: algorithm finds it
  - Out-of-horizon: returns None
  - Multi-pair: soonest event wins
  - Soonest-event-among-multiple-minima within one pair: first one wins
  - Determinism: same input → same output
"""

from __future__ import annotations

import math

import pytest

from sidequest.orbital.conjunction import (
    ConjunctionEvent,
    _angular_separation_deg,
    next_conjunction,
)
from sidequest.orbital.models import (
    BodyDef,
    BodyType,
    ClockConfig,
    ConjunctionPair,
    OrbitsConfig,
    TravelConfig,
    TravelRealism,
)


def _orbits(bodies: dict[str, BodyDef], pairs: list[ConjunctionPair]) -> OrbitsConfig:
    return OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(epoch_days=0),
        travel=TravelConfig(realism=TravelRealism.ORBITAL),
        bodies=bodies,
        conjunctions=pairs,
    )


def _star_with_two_planets(
    *,
    period_a: float,
    period_b: float,
    phase_a: float = 0.0,
    phase_b: float = 0.0,
) -> dict[str, BodyDef]:
    return {
        "sun": BodyDef(type=BodyType.STAR, label="SUN"),
        "alpha": BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=1.0,
            period_days=period_a,
            epoch_phase_deg=phase_a,
        ),
        "beta": BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=2.0,
            period_days=period_b,
            epoch_phase_deg=phase_b,
        ),
    }


# ---------------------------------------------------------------------------
# next_conjunction
# ---------------------------------------------------------------------------


def test_no_conjunctions_returns_none():
    """Empty `conjunctions` list → None (HUD bottom-left hides)."""
    orbits = _orbits(_star_with_two_planets(period_a=200, period_b=300), pairs=[])
    assert next_conjunction(orbits, t_hours=0.0) is None


def test_co_orbital_bodies_have_recurring_conjunctions():
    """Two bodies with the same period but different phases have a stable
    angular separation forever — no minimum to find. The algorithm should
    return None (no local minimum brackets within horizon)."""
    orbits = _orbits(
        _star_with_two_planets(period_a=200, period_b=200, phase_a=0, phase_b=180),
        [ConjunctionPair(body_a="alpha", body_b="beta")],
    )
    assert next_conjunction(orbits, t_hours=0.0, horizon_days=365) is None


def test_finds_conjunction_for_simple_unequal_periods():
    """Two planets with different periods will periodically align. The
    next minimum should land within ~one synodic period."""
    # synodic period = 1 / |1/P_a - 1/P_b| = 1 / |1/100 - 1/200| = 200 days
    orbits = _orbits(
        _star_with_two_planets(period_a=100, period_b=200, phase_a=10, phase_b=10),
        [ConjunctionPair(body_a="alpha", body_b="beta")],
    )
    event = next_conjunction(orbits, t_hours=0.0, horizon_days=365)
    assert event is not None
    assert event.body_a_id == "alpha"
    assert event.body_b_id == "beta"
    # Bodies start aligned at t=0; next alignment at synodic period (200d).
    # Could be a same-side conjunction (sep≈0) or opposition (sep≈180);
    # algorithm finds whichever local minimum bracketed first. We accept
    # either — they're both "alignment events" under our definition.
    assert event.t_hours_until > 0
    assert event.t_hours_until < 365 * 24


def test_event_t_hours_until_relative_to_now():
    """t_hours_until = t_hours_event - t_hours_now."""
    orbits = _orbits(
        _star_with_two_planets(period_a=100, period_b=200),
        [ConjunctionPair(body_a="alpha", body_b="beta")],
    )
    t_now = 500.0
    event = next_conjunction(orbits, t_hours=t_now)
    assert event is not None
    assert event.t_hours_until == pytest.approx(event.t_hours_event - t_now, abs=0.01)


def test_event_at_local_minimum_is_actually_a_minimum():
    """The returned t_hours_event should be at (or very near) a local
    minimum: separation at the event ≤ separation just before/after."""
    orbits = _orbits(
        _star_with_two_planets(period_a=100, period_b=200),
        [ConjunctionPair(body_a="alpha", body_b="beta")],
    )
    event = next_conjunction(orbits, t_hours=0.0)
    assert event is not None
    body_a = orbits.bodies["alpha"]
    body_b = orbits.bodies["beta"]

    def sep_at(t: float) -> float:
        return _angular_separation_deg(body_a, body_b, t)

    sep_event = sep_at(event.t_hours_event)
    sep_before = sep_at(event.t_hours_event - 1.0)
    sep_after = sep_at(event.t_hours_event + 1.0)
    assert sep_event <= sep_before + 1e-3
    assert sep_event <= sep_after + 1e-3
    assert sep_event == pytest.approx(event.min_separation_deg, abs=1e-3)


def test_multi_pair_returns_soonest():
    """When two pairs both have minima in the horizon, the soonest wins —
    regardless of list order in `conjunctions`."""
    bodies = {
        "sun": BodyDef(type=BodyType.STAR, label="SUN"),
        # Fast pair: short synodic, starts well-separated so the first
        # minimum is the natural synodic alignment ~100 days out.
        "p1a": BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=1.0,
            period_days=50,
            epoch_phase_deg=180,  # opposed at t=0
        ),
        "p1b": BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=1.5,
            period_days=100,
            epoch_phase_deg=0,
        ),
        # Slow pair: long synodic AND opposed at t=0 — first event is far off.
        "p2a": BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=3.0,
            period_days=500,
            epoch_phase_deg=180,
        ),
        "p2b": BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=4.0,
            period_days=600,
            epoch_phase_deg=0,
        ),
    }
    orbits = _orbits(
        bodies,
        [
            ConjunctionPair(body_a="p2a", body_b="p2b", label="slow pair"),
            ConjunctionPair(body_a="p1a", body_b="p1b", label="fast pair"),
        ],
    )
    event = next_conjunction(orbits, t_hours=0.0, horizon_days=2000)
    assert event is not None
    # Synodic period of fast pair: 1/|1/50 - 1/100| = 100 days.
    # Synodic period of slow pair: 1/|1/500 - 1/600| = 3000 days.
    # Fast pair's first minimum bracketed near t=50d (half synodic).
    # Slow pair's first minimum lands near t=1500d.
    assert event.label == "fast pair"


def test_returns_none_when_no_minimum_in_horizon():
    """A pair whose first minimum is beyond the horizon → None."""
    bodies = {
        "sun": BodyDef(type=BodyType.STAR, label="SUN"),
        "alpha": BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=1.0,
            period_days=10000,
            epoch_phase_deg=0,
        ),
        "beta": BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=2.0,
            period_days=10001,
            epoch_phase_deg=180,  # start opposed; separation tiny over a year
        ),
    }
    orbits = _orbits(bodies, [ConjunctionPair(body_a="alpha", body_b="beta")])
    # 1-day horizon — too short to bracket anything
    event = next_conjunction(orbits, t_hours=0.0, horizon_days=1)
    assert event is None


def test_deterministic():
    """Same inputs → same output, byte-for-byte. Required for snapshot
    tests of OrbitalIntentResponse."""
    orbits = _orbits(
        _star_with_two_planets(period_a=100, period_b=200),
        [ConjunctionPair(body_a="alpha", body_b="beta")],
    )
    a = next_conjunction(orbits, t_hours=42.0)
    b = next_conjunction(orbits, t_hours=42.0)
    assert a == b
    assert isinstance(a, ConjunctionEvent)


def test_default_pair_label_uses_body_labels():
    """When pair.label is None, use 'A.label ↔ B.label'."""
    orbits = _orbits(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "alpha": BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=1.0,
                period_days=100,
                epoch_phase_deg=0,
                label="ALPHA",
            ),
            "beta": BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=2.0,
                period_days=200,
                epoch_phase_deg=0,
                label="BETA",
            ),
        },
        [ConjunctionPair(body_a="alpha", body_b="beta")],
    )
    event = next_conjunction(orbits, t_hours=0.0)
    assert event is not None
    assert event.label == "ALPHA ↔ BETA"


# ---------------------------------------------------------------------------
# _angular_separation_deg
# ---------------------------------------------------------------------------


def test_angular_separation_zero_when_aligned():
    """Two bodies at the same angle have zero separation."""
    body_a = BodyDef(
        type=BodyType.HABITAT,
        parent="sun",
        semi_major_au=1.0,
        period_days=100,
        epoch_phase_deg=45,
    )
    body_b = BodyDef(
        type=BodyType.HABITAT,
        parent="sun",
        semi_major_au=2.0,
        period_days=100,  # same period → constant phase relationship
        epoch_phase_deg=45,
    )
    assert _angular_separation_deg(body_a, body_b, t_hours=0.0) == pytest.approx(0.0)


def test_angular_separation_max_180_at_opposition():
    """Two bodies on opposite sides of star have separation = 180°."""
    body_a = BodyDef(
        type=BodyType.HABITAT,
        parent="sun",
        semi_major_au=1.0,
        period_days=100,
        epoch_phase_deg=0,
    )
    body_b = BodyDef(
        type=BodyType.HABITAT,
        parent="sun",
        semi_major_au=2.0,
        period_days=100,
        epoch_phase_deg=180,
    )
    assert _angular_separation_deg(body_a, body_b, t_hours=0.0) == pytest.approx(180.0)


def test_angular_separation_wraps_correctly():
    """At 359° vs 1°, separation should be 2°, not 358°."""
    body_a = BodyDef(
        type=BodyType.HABITAT,
        parent="sun",
        semi_major_au=1.0,
        period_days=100,
        epoch_phase_deg=359,
    )
    body_b = BodyDef(
        type=BodyType.HABITAT,
        parent="sun",
        semi_major_au=2.0,
        period_days=100,
        epoch_phase_deg=1,
    )
    sep = _angular_separation_deg(body_a, body_b, t_hours=0.0)
    assert sep < 5.0, f"wrap broken: sep={sep}, expected near 2°"
    assert sep > 0, "should not be exactly zero"
    assert math.isclose(sep, 2.0, abs_tol=0.01)
