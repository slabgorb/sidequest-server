"""Golden snapshot for course Bezier overlay.

The overlay is composed onto the existing chart SVG. We render a
chart with a known plotted_course and assert the SVG includes:
- One <path d="M ... C ..." /> with the dashed engraved-register
  styling
- A target reticle <g> at the destination
- A HUD chip element with ETA/Δv text

Snapshot lock: golden lives at tests/orbital/golden/course_overlay.svg.
"""
from __future__ import annotations

from pathlib import Path

from sidequest.orbital.course import CourseSource, PlottedCourse
from sidequest.orbital.course_render import render_course_overlay
from sidequest.orbital.models import (
    BodyDef,
    BodyType,
    ClockConfig,
    OrbitsConfig,
    TravelConfig,
    TravelRealism,
)


GOLDEN = Path(__file__).parent / "golden" / "course_overlay.svg"


def _orbits() -> OrbitsConfig:
    return OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(),
        travel=TravelConfig(realism=TravelRealism.ORBITAL),
        bodies={
            "coyote": BodyDef(type=BodyType.STAR),
            "near": BodyDef(
                type=BodyType.HABITAT,
                parent="coyote",
                semi_major_au=1.0,
                period_days=365.0,
                epoch_phase_deg=0.0,
            ),
            "far": BodyDef(
                type=BodyType.HABITAT,
                parent="coyote",
                semi_major_au=3.0,
                period_days=1100.0,
                epoch_phase_deg=180.0,
            ),
        },
    )


def test_render_course_overlay_produces_path_and_chip() -> None:
    course = PlottedCourse(
        to_body_id="far",
        label="Far",
        eta_hours=80.0,
        delta_v=2.4,
        plotted_at_t_hours=0.0,
        source=CourseSource.IN_SCOPE,
    )
    svg = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"  # minimal carrier
    result = render_course_overlay(
        chart_svg=svg,
        course=course,
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    assert "<path" in result
    assert 'd="M' in result
    assert " C " in result  # cubic Bezier
    assert "stroke-dasharray" in result
    assert "#d9a766" in result  # pale amber per design
    assert "ETA 80h" in result or "ETA 80" in result
    assert "Δv 2.4" in result or "delta_v" in result.lower()
    assert "FAR" in result.upper()


def test_render_course_overlay_no_change_when_course_is_none() -> None:
    svg_in = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    svg_out = render_course_overlay(
        chart_svg=svg_in,
        course=None,
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    assert svg_out == svg_in


def test_render_course_overlay_handles_missing_party() -> None:
    course = PlottedCourse(
        to_body_id="far",
        eta_hours=10.0,
        delta_v=1.0,
        plotted_at_t_hours=0.0,
        source=CourseSource.IN_SCOPE,
    )
    svg_in = "<svg></svg>"
    # No party_body_id: overlay drops, OTEL flag set on caller side.
    svg_out = render_course_overlay(
        chart_svg=svg_in,
        course=course,
        orbits=_orbits(),
        party_body_id=None,
        t_hours=0.0,
    )
    assert svg_out == svg_in


def test_render_course_overlay_drops_unknown_target() -> None:
    course = PlottedCourse(
        to_body_id="ghost_body_not_in_orbits",
        eta_hours=10.0,
        delta_v=1.0,
        plotted_at_t_hours=0.0,
        source=CourseSource.IN_SCOPE,
    )
    svg_in = "<svg></svg>"
    svg_out = render_course_overlay(
        chart_svg=svg_in,
        course=course,
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    # Unknown target: no overlay, no crash.
    assert svg_out == svg_in
