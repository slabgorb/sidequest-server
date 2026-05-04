"""Pydantic model tests for orbits.yaml + chart.yaml schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.orbital.models import (
    Annotation,
    BodyDef,
    BodyType,
    ChartConfig,
    ClockConfig,
    ConjunctionPair,
    OrbitsConfig,
    TravelConfig,
    TravelRealism,
)


def _orbiting(parent: str, period: float = 100.0, phase: float = 0.0) -> BodyDef:
    return BodyDef(
        type=BodyType.HABITAT,
        parent=parent,
        semi_major_au=1.0,
        period_days=period,
        epoch_phase_deg=phase,
    )


def test_minimal_orbits_config_loads():
    cfg = OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(epoch_days=0),
        travel=TravelConfig(realism=TravelRealism.ORBITAL),
        bodies={"coyote": BodyDef(type=BodyType.STAR)},
    )
    assert cfg.bodies["coyote"].type == BodyType.STAR


def test_orbiting_body_requires_orbital_params():
    with pytest.raises(ValidationError, match="semi_major_au"):
        OrbitsConfig(
            version="0.1.0",
            clock=ClockConfig(epoch_days=0),
            travel=TravelConfig(realism=TravelRealism.ORBITAL),
            bodies={
                "coyote": BodyDef(type=BodyType.STAR),
                "red_prospect": BodyDef(type=BodyType.COMPANION, parent="coyote"),
            },
        )


def test_arc_belt_requires_arc_extent():
    with pytest.raises(ValidationError, match="arc_extent_deg"):
        BodyDef(
            type=BodyType.ARC_BELT,
            parent="coyote",
            semi_major_au=1.5,
            period_days=600,
            epoch_phase_deg=30,
            hazard=True,
        )


def test_eccentricity_default_zero():
    body = BodyDef(
        type=BodyType.HABITAT,
        parent="coyote",
        semi_major_au=1.0,
        period_days=365,
        epoch_phase_deg=0,
    )
    assert body.eccentricity == 0.0


def test_unknown_parent_rejected():
    """A body cannot have a parent that does not exist in the bodies map."""
    with pytest.raises(ValidationError, match="unknown parent"):
        OrbitsConfig(
            version="0.1.0",
            clock=ClockConfig(epoch_days=0),
            travel=TravelConfig(realism=TravelRealism.ORBITAL),
            bodies={
                "moon": BodyDef(
                    type=BodyType.HABITAT,
                    parent="ghost",
                    semi_major_au=0.04,
                    period_days=6,
                    epoch_phase_deg=0,
                ),
            },
        )


def test_realism_default_narrative():
    """Genre-default tier is `narrative` per spec — locked decision 1."""
    cfg = TravelConfig()
    assert cfg.realism == TravelRealism.NARRATIVE
    assert cfg.travel_speed_factor == 1.0
    assert cfg.danger_density == 0.0


def test_chart_engraved_label():
    annot = Annotation(
        kind="engraved_label",
        text="the Last Drift",
        curve_along="orbit_outermost",
    )
    assert annot.kind == "engraved_label"


def test_chart_glyph():
    annot = Annotation(
        kind="glyph",
        text="?",
        at={"ra_deg": 135, "au": 5.0},
        caption="absent gate",
    )
    assert annot.at["au"] == 5.0


def test_chart_config_loads_list():
    cfg = ChartConfig(
        version="0.1.0",
        annotations=[
            Annotation(kind="engraved_label", text="x", curve_along="orbit_3"),
            Annotation(kind="glyph", text="?", at={"ra_deg": 0, "au": 1}),
        ],
    )
    assert len(cfg.annotations) == 2


def test_unknown_annotation_kind_fails_at_load():
    """Per CLAUDE.md no-silent-fallbacks: an unknown annotation kind must
    raise at chart-load, not silently disappear at render time. The render
    layer is the wrong place to find this — by the time a chart reaches the
    renderer it should be valid."""
    import pytest

    with pytest.raises(ValueError, match="unknown annotation kind"):
        Annotation(kind="freeform_chalk", text="?")


def test_conjunctions_default_empty():
    """A world without authored conjunctions still loads cleanly."""
    cfg = OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(epoch_days=0),
        travel=TravelConfig(),
        bodies={"sun": BodyDef(type=BodyType.STAR)},
    )
    assert cfg.conjunctions == []


def test_conjunction_pair_loads_with_valid_bodies():
    cfg = OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(epoch_days=0),
        travel=TravelConfig(),
        bodies={
            "sun": BodyDef(type=BodyType.STAR),
            "alpha": _orbiting("sun"),
            "beta": _orbiting("sun", period=200, phase=30),
        },
        conjunctions=[ConjunctionPair(body_a="alpha", body_b="beta", label="A↔B")],
    )
    assert len(cfg.conjunctions) == 1
    assert cfg.conjunctions[0].label == "A↔B"


def test_conjunction_pair_rejects_unknown_body():
    with pytest.raises(ValidationError, match="not in bodies"):
        OrbitsConfig(
            version="0.1.0",
            clock=ClockConfig(epoch_days=0),
            travel=TravelConfig(),
            bodies={
                "sun": BodyDef(type=BodyType.STAR),
                "alpha": _orbiting("sun"),
            },
            conjunctions=[ConjunctionPair(body_a="alpha", body_b="ghost")],
        )


def test_conjunction_pair_rejects_self_pair():
    """A body cannot conjunct with itself."""
    with pytest.raises(ValidationError, match="two different bodies"):
        OrbitsConfig(
            version="0.1.0",
            clock=ClockConfig(epoch_days=0),
            travel=TravelConfig(),
            bodies={
                "sun": BodyDef(type=BodyType.STAR),
                "alpha": _orbiting("sun"),
            },
            conjunctions=[ConjunctionPair(body_a="alpha", body_b="alpha")],
        )


def test_conjunction_pair_rejects_bodies_with_no_common_ancestor():
    """Bodies in disjoint orbital subtrees can't have a meaningful angular
    separation. The validator must catch this at load."""
    with pytest.raises(ValidationError, match="common ancestor"):
        OrbitsConfig(
            version="0.1.0",
            clock=ClockConfig(epoch_days=0),
            travel=TravelConfig(),
            bodies={
                # Two independent root systems (impossible IRL, but the
                # validator should catch the mistake at load time anyway).
                "sun_a": BodyDef(type=BodyType.STAR),
                "sun_b": BodyDef(type=BodyType.STAR),
                "alpha": _orbiting("sun_a"),
                "beta": _orbiting("sun_b"),
            },
            conjunctions=[ConjunctionPair(body_a="alpha", body_b="beta")],
        )


def test_all_known_annotation_kinds_load():
    """Forward-compat reminder: when adding a new annotation kind to the
    renderer, also add it to KNOWN_ANNOTATION_KINDS — and this test will
    let you verify both halves landed together."""
    from sidequest.orbital.models import KNOWN_ANNOTATION_KINDS

    expected = {
        "engraved_label",
        "glyph",
        "scale_ruler",
        "bearing_marks",
        "anomaly_marker",
        "lagrange_point",
        "flight_corridor",
        "callout_label",
    }
    assert expected == KNOWN_ANNOTATION_KINDS, (
        "KNOWN_ANNOTATION_KINDS drifted from this test's expectation. "
        "If a new kind is intentional, update both this test AND ensure "
        "_render_annotation in render.py handles it."
    )
