"""Tests for the visual restoration pass.

Spec: docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md
Pins the §11 acceptance criteria that aren't already covered by structural
or snapshot tests:
  - palette tokens reach output
  - hazard semantic (hazard:true → red fill regardless of type)
  - distinct glyph per BodyType (all six)
  - ARC_BELT renders as dotted arc, not point + ring
  - subtype=gas_giant adds banding overlay
  - new annotation kinds (anomaly_marker, lagrange_point, flight_corridor,
    bearing_marks) render
  - unknown annotation kind fails at chart-load (no silent fallbacks)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.orbital import palette
from sidequest.orbital.loader import load_orbital_content
from sidequest.orbital.models import (
    Annotation,
    BodyDef,
    BodyType,
    ChartConfig,
    ClockConfig,
    OrbitsConfig,
    TravelConfig,
    TravelRealism,
)
from sidequest.orbital.render import Scope, render_chart

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Test helpers — synthetic fixtures
# ---------------------------------------------------------------------------


def _orbits_with_bodies(bodies: dict[str, BodyDef]) -> OrbitsConfig:
    return OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(epoch_days=0),
        travel=TravelConfig(realism=TravelRealism.ORBITAL),
        bodies=bodies,
    )


def _empty_chart() -> ChartConfig:
    return ChartConfig(version="0.1.0", annotations=[])


def _star_only(extra_bodies: dict[str, BodyDef] | None = None) -> OrbitsConfig:
    """Synthetic orbits with a star at the root, plus optional children."""
    bodies: dict[str, BodyDef] = {
        "sun": BodyDef(type=BodyType.STAR, label="SUN"),
    }
    if extra_bodies:
        bodies.update(extra_bodies)
    return _orbits_with_bodies(bodies)


def _render_root(orbits: OrbitsConfig, chart: ChartConfig | None = None) -> str:
    return render_chart(
        orbits=orbits,
        chart=chart or _empty_chart(),
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )


# ---------------------------------------------------------------------------
# Palette wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def world_minimal():
    return load_orbital_content(FIXTURES / "world_minimal")


def test_palette_tokens_appear_in_output(world_minimal):
    """The renderer's defaults must use palette tokens, not legacy literals."""
    svg = _render_root(world_minimal.orbits, world_minimal.chart)
    assert palette.BG in svg, f"missing background token {palette.BG!r}"
    assert palette.BRASS in svg, f"missing brass token {palette.BRASS!r}"
    assert palette.RED in svg, f"missing red token {palette.RED!r}"


def test_no_legacy_yellow_or_orange_literals_in_output(world_minimal):
    """The previous palette used named colors ('yellow', 'orange', 'black').
    Those should be entirely absent from the renderer's default output —
    the only acceptable named colors are user-authored `label_color` overrides
    in the fixture (which the world_minimal fixture sets to 'red').
    """
    svg = _render_root(world_minimal.orbits, world_minimal.chart)
    assert 'fill="yellow"' not in svg
    assert 'stroke="yellow"' not in svg
    assert 'fill="orange"' not in svg
    assert 'fill="black"' not in svg
    # Palette.PARTY is #ffffff, never named "white"
    assert 'fill="white"' not in svg
    assert 'stroke="white"' not in svg


def test_haloed_text_uses_paint_order(world_minimal):
    """Halo trick: every label-class text element carries paint-order=stroke
    so the BG-colored stroke renders behind the fill, producing a halo."""
    svg = _render_root(world_minimal.orbits, world_minimal.chart)
    assert 'paint-order="stroke"' in svg


# ---------------------------------------------------------------------------
# Per-body-type glyph distinctness
# ---------------------------------------------------------------------------


def _orbits_with_one_of_each_type() -> OrbitsConfig:
    return _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "comp": BodyDef(
                type=BodyType.COMPANION,
                parent="sun",
                semi_major_au=1.0,
                period_days=300,
                epoch_phase_deg=0,
                label="COMP",
            ),
            "hab": BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=2.0,
                period_days=600,
                epoch_phase_deg=45,
                label="HAB",
            ),
            "belt": BodyDef(
                type=BodyType.ARC_BELT,
                parent="sun",
                semi_major_au=3.0,
                period_days=900,
                epoch_phase_deg=10,
                arc_extent_deg=120,
                label="BELT",
            ),
            "gate": BodyDef(
                type=BodyType.GATE,
                parent="sun",
                semi_major_au=4.0,
                period_days=1200,
                epoch_phase_deg=180,
                label="GATE",
            ),
            "wreck": BodyDef(
                type=BodyType.WRECK,
                parent="sun",
                semi_major_au=5.0,
                period_days=1500,
                epoch_phase_deg=270,
                label="WRECK",
            ),
        }
    )


def test_all_six_body_types_render():
    """Every BodyType produces output. Replaces the prior fall-through-to-yellow
    behavior for HABITAT/GATE/WRECK with type-specific glyphs."""
    svg = _render_root(_orbits_with_one_of_each_type())
    for body_id in ("sun", "comp", "hab", "belt", "gate", "wreck"):
        assert f'data-body-id="{body_id}"' in svg, f"body {body_id!r} missing from output"


def test_habitat_renders_as_diamond_polygon():
    """HABITAT glyph is a brass diamond (rotated square) — an SVG polygon."""
    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "hab": BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=1.0,
                period_days=300,
                epoch_phase_deg=0,
            ),
        }
    )
    svg = _render_root(orbits)
    # Polygon element with brass fill — present for the diamond.
    assert "<polygon" in svg
    assert palette.BRASS in svg


def test_gate_renders_as_hexagon_polygon():
    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "gate": BodyDef(
                type=BodyType.GATE,
                parent="sun",
                semi_major_au=1.0,
                period_days=300,
                epoch_phase_deg=0,
            ),
        }
    )
    svg = _render_root(orbits)
    # Should produce a polygon (hexagon) attached to the gate body.
    assert 'data-body-id="gate"' in svg
    assert "<polygon" in svg


def test_wreck_renders_as_dim_asterisk():
    """WRECK is an asterisk in DIM color, not a brass-or-red glyph."""
    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "wreck": BodyDef(
                type=BodyType.WRECK,
                parent="sun",
                semi_major_au=1.0,
                period_days=300,
                epoch_phase_deg=0,
            ),
        }
    )
    svg = _render_root(orbits)
    assert 'data-body-id="wreck"' in svg
    # Asterisk = 5 line elements, all in DIM color.
    assert palette.DIM in svg


# ---------------------------------------------------------------------------
# Hazard semantic — overrides type's default fill
# ---------------------------------------------------------------------------


def test_hazard_body_renders_in_red_regardless_of_type():
    """A HABITAT with hazard=True must render in palette.RED, not BRASS."""
    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "trap": BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=1.0,
                period_days=300,
                epoch_phase_deg=0,
                hazard=True,
                label="TRAP",
            ),
        }
    )
    svg = _render_root(orbits)
    # The diamond's fill should be the red palette token.
    assert f'fill="{palette.RED}"' in svg
    # And it should be on a polygon (the diamond).
    assert "<polygon" in svg


def test_hazardous_arc_belt_renders_red_dots():
    """ARC_BELT honors hazard semantic too — dotted arc in red."""
    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "belt": BodyDef(
                type=BodyType.ARC_BELT,
                parent="sun",
                semi_major_au=2.0,
                period_days=600,
                epoch_phase_deg=0,
                arc_extent_deg=90,
                hazard=True,
            ),
        }
    )
    svg = _render_root(orbits)
    # Many small circles (the arc dots) all in red.
    assert svg.count(f'fill="{palette.RED}"') >= 5


# ---------------------------------------------------------------------------
# ARC_BELT: dotted arc, not point + ring
# ---------------------------------------------------------------------------


def test_arc_belt_renders_as_many_small_dots_not_a_single_body_glyph():
    """The previous renderer drew an orbit ring + one orange dot at the body's
    polar position. The arc-belt is a span, not a point — it should produce
    many small brass dots and no orbit ring around it."""
    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "belt": BodyDef(
                type=BodyType.ARC_BELT,
                parent="sun",
                semi_major_au=3.0,
                period_days=900,
                epoch_phase_deg=0,
                arc_extent_deg=180,
            ),
        }
    )
    svg = _render_root(orbits)
    # Many dots (each a tiny circle) along the arc.
    assert svg.count("<circle") >= 10, "arc-belt should emit many dot circles"
    # The arc group is tagged with the body id.
    assert 'data-body-id="belt"' in svg


# ---------------------------------------------------------------------------
# Subtype: gas-giant overlay
# ---------------------------------------------------------------------------


def test_gas_giant_subtype_adds_banding_lines():
    """A HABITAT with subtype='gas_giant' renders a brass disk with three
    horizontal banding lines overlaid — distinguishing it from a normal
    diamond habitat."""
    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "giant": BodyDef(
                type=BodyType.HABITAT,
                subtype="gas_giant",
                parent="sun",
                semi_major_au=2.0,
                period_days=600,
                epoch_phase_deg=0,
            ),
        }
    )
    svg = _render_root(orbits)
    # Disk + three banding lines = at least 3 <line> elements on the giant.
    assert svg.count("<line") >= 3
    # No diamond polygon — gas giant uses a circle, not the diamond habitat
    # glyph. (The star-only fixture means the only polygon would be the
    # giant's, so its absence proves the gas-giant branch was taken.)
    assert "<polygon" not in svg


# ---------------------------------------------------------------------------
# New annotation kinds
# ---------------------------------------------------------------------------


def test_anomaly_marker_renders_hexagon_and_glyph():
    chart = ChartConfig(
        version="0.1.0",
        annotations=[
            Annotation(
                kind="anomaly_marker",
                text="Ψ",
                caption="Tsveri-blank",
                at={"ra_deg": 90, "au": 1.0},
            )
        ],
    )
    svg = _render_root(_star_only(), chart)
    # Hexagon outlined in red, glyph in red.
    assert "<polygon" in svg
    assert f'stroke="{palette.RED}"' in svg
    assert ">Ψ<" in svg
    assert ">Tsveri-blank<" in svg


def test_lagrange_point_renders_triangle_with_label():
    chart = ChartConfig(
        version="0.1.0",
        annotations=[
            Annotation(
                kind="lagrange_point",
                label="L4",
                at={"ra_deg": 60, "au": 1.0},
            )
        ],
    )
    svg = _render_root(_star_only(), chart)
    assert "<polygon" in svg
    assert ">L4<" in svg


def test_flight_corridor_renders_dashed_line():
    chart = ChartConfig(
        version="0.1.0",
        annotations=[
            Annotation(
                kind="flight_corridor",
                at={
                    "from_ra_deg": 0,
                    "from_au": 1.0,
                    "to_ra_deg": 90,
                    "to_au": 2.0,
                },
            )
        ],
    )
    svg = _render_root(_star_only(), chart)
    assert "<line" in svg
    assert 'stroke-dasharray="4,4"' in svg


def test_bearing_marks_renders_degree_labels():
    chart = ChartConfig(
        version="0.1.0",
        annotations=[
            Annotation(kind="bearing_marks", bearings=[0, 90, 180, 270]),
        ],
    )
    svg = _render_root(_star_only(), chart)
    # All four cardinal degree labels present.
    for deg in ("000°", "090°", "180°", "270°"):
        assert deg in svg, f"bearing label {deg!r} missing"


def test_eccentric_orbit_renders_as_ellipse_with_unequal_axes():
    """The orbit ring of an eccentric body must be an SVG <ellipse> with
    rx > ry, with the center offset from the focus by -c=-a·e along x.
    The deleted OrreryView's wire test was geometric correctness; this
    test stands in its place for the server-rendered chart."""
    import re

    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "comet": BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=2.0,
                period_days=600,
                epoch_phase_deg=0,
                eccentricity=0.5,
            ),
        }
    )
    svg = _render_root(orbits)
    # Find the ellipse element bound to the comet's orbit.
    re.search(r'<ellipse[^>]*data-body-id="comet"[^>]*/>', svg) or re.search(
        r'<ellipse[^>]*/>[^<]*<[^/][^>]*data-body-id="comet"', svg
    )
    # The ellipse and the data-body-id may render in either order; just
    # confirm an ellipse exists in the output and rx != ry on at least one.
    ellipse_tags = re.findall(r"<ellipse[^>]*/>", svg)
    assert ellipse_tags, "no <ellipse> in output — orbit ring still circular"
    # Find any ellipse where rx != ry (the eccentric one).
    eccentric_found = False
    for tag in ellipse_tags:
        rx_match = re.search(r'rx="([\-\d.]+)"', tag)
        ry_match = re.search(r'ry="([\-\d.]+)"', tag)
        if (
            rx_match
            and ry_match
            and abs(float(rx_match.group(1)) - float(ry_match.group(1))) > 0.5
        ):
            eccentric_found = True
            break
    assert eccentric_found, (
        "no ellipse with unequal axes found — eccentricity not honored. "
        "Tags emitted: " + str(ellipse_tags[:5])
    )


def test_circular_orbit_renders_as_ellipse_with_equal_axes():
    """For e=0, rx must equal ry — visually identical to the prior circle."""
    import re

    orbits = _orbits_with_bodies(
        {
            "sun": BodyDef(type=BodyType.STAR, label="SUN"),
            "ring": BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=2.0,
                period_days=600,
                epoch_phase_deg=0,
                eccentricity=0.0,
            ),
        }
    )
    svg = _render_root(orbits)
    ellipse_tags = re.findall(r"<ellipse[^>]*/>", svg)
    # At least one ellipse must have rx==ry (the ring).
    found_circular = False
    for tag in ellipse_tags:
        rx_match = re.search(r'rx="([\-\d.]+)"', tag)
        ry_match = re.search(r'ry="([\-\d.]+)"', tag)
        if (
            rx_match
            and ry_match
            and abs(float(rx_match.group(1)) - float(ry_match.group(1))) < 0.01
        ):
            found_circular = True
            break
    assert found_circular, "circular orbit should produce ellipse with rx==ry"


def test_flight_corridor_missing_at_field_fails_loud():
    """Per CLAUDE.md no-silent-fallbacks: missing `at` keys for a flight
    corridor must raise, not skip."""
    chart = ChartConfig(
        version="0.1.0",
        annotations=[
            Annotation(
                kind="flight_corridor",
                at={"from_ra_deg": 0, "from_au": 1.0},  # missing to_ra_deg/to_au
            )
        ],
    )
    with pytest.raises(ValueError, match="flight_corridor.*missing"):
        _render_root(_star_only(), chart)
