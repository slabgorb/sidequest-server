"""Tests for course Bezier overlay composer.

The overlay is composed onto the existing chart SVG. Aesthetic spec lives
at docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md
(Star Wars A-New-Hope HUD register: amber #f5d020 + red #e62a18 on black,
Orbitron + VT323 typography). The course-overlay rewrite (2026-05-03) brings
this last chart layer into alignment with that spec.

Suite covers:
- Structural fragments (path, reticle, chip) are emitted
- Palette + typography come from sidequest.orbital.palette via CSS custom
  properties on the layer-course root
- HUD chip uses the auto-sizing sentinel attrs the client JS measures
- Reticle group uses the documented `id="course-target"` + transform pattern
- All four drop reasons (none_course / unknown_party / unknown_destination /
  root_party) return the input SVG unchanged
- Reticle position aligns with the destination body's rendered glyph center
"""

from __future__ import annotations

import re

from sidequest.orbital import palette
from sidequest.orbital.course import CourseSource, PlottedCourse
from sidequest.orbital.course_render import (
    _au_to_px_scale,
    _body_xy,
    _resolve_drop_reason,
    render_course_overlay,
)
from sidequest.orbital.models import (
    BodyDef,
    BodyType,
    ClockConfig,
    OrbitsConfig,
    TravelConfig,
    TravelRealism,
)


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


def _course(to_body_id: str = "far", label: str | None = "Far") -> PlottedCourse:
    return PlottedCourse(
        to_body_id=to_body_id,
        label=label,
        eta_hours=80.0,
        delta_v=2.4,
        plotted_at_t_hours=0.0,
        source=CourseSource.IN_SCOPE,
    )


# ---------------------------------------------------------------------------
# Structural emission
# ---------------------------------------------------------------------------


def test_render_course_overlay_produces_path_and_chip() -> None:
    svg = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    result = render_course_overlay(
        chart_svg=svg,
        course=_course(),
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    # Bezier arc
    assert "<path" in result
    assert 'd="M' in result
    assert " C " in result
    assert "stroke-dasharray" in result
    # ETA + Δv text
    assert "ETA 80h" in result
    assert "Δv 2.4" in result
    # Label is uppercased
    assert "FAR" in result


def test_layer_emits_palette_css_custom_properties() -> None:
    """Layer root surfaces palette tokens as CSS variables so a host page
    can override them without re-rendering server-side."""
    svg = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    result = render_course_overlay(
        chart_svg=svg,
        course=_course(),
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    assert 'id="layer-course"' in result
    # Each token should appear in the layer style attribute.
    assert f"--course-stroke: {palette.BRASS}" in result
    assert f"--course-reticle: {palette.RED}" in result
    assert f"--course-chip-bg: {palette.BG}" in result
    assert f"--course-chip-stroke: {palette.BRASS}" in result
    # Element fills/strokes reference the variables, not raw hexes inline.
    assert "var(--course-stroke)" in result
    assert "var(--course-reticle)" in result


def test_chip_uses_chart_typography() -> None:
    """HUD chip text uses Orbitron (label) + VT323 (detail) per palette,
    not the old hardcoded `monospace`."""
    svg = "<svg></svg>"
    result = render_course_overlay(
        chart_svg=svg,
        course=_course(),
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    assert palette.FONT_DISPLAY in result  # Orbitron, monospace
    assert palette.FONT_NUMERIC in result  # VT323, monospace


def test_chip_carries_auto_sizing_sentinels() -> None:
    """Chip needs sentinel attrs so the client-side getBBox() pass can
    find and resize it. The server emits a placeholder width."""
    svg = "<svg></svg>"
    result = render_course_overlay(
        chart_svg=svg,
        course=_course(),
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    assert 'data-course-chip="auto"' in result
    assert 'data-course-chip-rect=""' in result
    assert 'data-text-id="label"' in result
    assert 'data-text-id="detail"' in result


def test_reticle_uses_translate_transform_pattern() -> None:
    """Reticle group uses `<g id="course-target" transform="translate(X,Y)">`
    so coordinate-alignment tests can extract its position by regex."""
    svg = "<svg></svg>"
    result = render_course_overlay(
        chart_svg=svg,
        course=_course(),
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    assert re.search(
        r'id="course-target"\s+transform="translate\([-\d.]+,[-\d.]+\)"',
        result,
    ), f"reticle group not found / wrong shape in:\n{result[:600]}"


# ---------------------------------------------------------------------------
# Drop matrix — all four reasons return the input SVG unchanged
# ---------------------------------------------------------------------------


def test_render_course_overlay_drops_when_course_is_none() -> None:
    svg_in = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    svg_out = render_course_overlay(
        chart_svg=svg_in,
        course=None,
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    assert svg_out == svg_in


def test_render_course_overlay_drops_when_party_unknown() -> None:
    svg_in = "<svg></svg>"
    svg_out = render_course_overlay(
        chart_svg=svg_in,
        course=_course(),
        orbits=_orbits(),
        party_body_id=None,
        t_hours=0.0,
    )
    assert svg_out == svg_in


def test_render_course_overlay_drops_when_destination_unknown() -> None:
    svg_in = "<svg></svg>"
    svg_out = render_course_overlay(
        chart_svg=svg_in,
        course=_course(to_body_id="ghost_body_not_in_orbits"),
        orbits=_orbits(),
        party_body_id="near",
        t_hours=0.0,
    )
    assert svg_out == svg_in


def test_render_course_overlay_drops_when_party_is_root_body() -> None:
    """Behavior change (2026-05-03): root party drops instead of drawing
    from origin (0, 0). A root body has no orbital position; the prior
    fallback produced a misleading arc anchored at the star."""
    svg_in = "<svg></svg>"
    svg_out = render_course_overlay(
        chart_svg=svg_in,
        course=_course(),
        orbits=_orbits(),
        party_body_id="coyote",  # root star
        t_hours=0.0,
    )
    assert svg_out == svg_in


# ---------------------------------------------------------------------------
# _resolve_drop_reason — direct enum coverage so OTEL/intent.py can read
# the same string the renderer used to drop.
# ---------------------------------------------------------------------------


def test_resolve_drop_reason_none_course() -> None:
    assert (
        _resolve_drop_reason(course=None, orbits=_orbits(), party_body_id="near") == "none_course"
    )


def test_resolve_drop_reason_unknown_party() -> None:
    assert (
        _resolve_drop_reason(course=_course(), orbits=_orbits(), party_body_id="not_a_body")
        == "unknown_party"
    )
    assert (
        _resolve_drop_reason(course=_course(), orbits=_orbits(), party_body_id=None)
        == "unknown_party"
    )


def test_resolve_drop_reason_root_party() -> None:
    """Root-party check fires before unknown-destination — a root party
    with a bad destination is still the bigger fixable cause."""
    assert (
        _resolve_drop_reason(
            course=_course(to_body_id="ghost"),
            orbits=_orbits(),
            party_body_id="coyote",
        )
        == "root_party"
    )


def test_resolve_drop_reason_unknown_destination() -> None:
    assert (
        _resolve_drop_reason(
            course=_course(to_body_id="ghost_body"),
            orbits=_orbits(),
            party_body_id="near",
        )
        == "unknown_destination"
    )


def test_resolve_drop_reason_returns_none_on_valid_input() -> None:
    assert _resolve_drop_reason(course=_course(), orbits=_orbits(), party_body_id="near") is None


# ---------------------------------------------------------------------------
# Reticle ↔ destination body coordinate alignment
# ---------------------------------------------------------------------------


def test_reticle_lands_on_destination_body() -> None:
    """Reticle center matches the destination body's rendered position
    within 0.5px. Catches drift between the renderer's body math and
    the overlay's coordinate computation."""
    orbits = _orbits()
    party_id = "near"
    dest_id = "far"
    t_hours = 0.0

    chart_stub = '<svg viewBox="-400 -400 800 800"></svg>'
    out = render_course_overlay(
        chart_svg=chart_stub,
        course=_course(to_body_id=dest_id),
        orbits=orbits,
        party_body_id=party_id,
        t_hours=t_hours,
    )

    root_id = next(bid for bid, b in orbits.bodies.items() if b.parent is None)
    scale = _au_to_px_scale(orbits, root_id)
    expected = _body_xy(orbits, dest_id, t_hours, scale)
    assert expected is not None
    ex, ey = expected

    m = re.search(r'id="course-target"\s+transform="translate\(([-\d.]+),([-\d.]+)\)"', out)
    assert m, f"course-target group not found in overlay output:\n{out[:600]}"
    rx, ry = float(m.group(1)), float(m.group(2))

    assert abs(rx - ex) <= 0.5, f"reticle x off by {rx - ex:.3f}px"
    assert abs(ry - ey) <= 0.5, f"reticle y off by {ry - ey:.3f}px"
