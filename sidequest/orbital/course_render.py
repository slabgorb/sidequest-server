"""Compose the course Bezier overlay onto an existing chart SVG.

Pure function: takes the chart SVG string + course state, returns a
new SVG string with the overlay layer inserted just before the
closing </svg> tag.

Design alignment (handoff 2026-05-03):
- Visual register matches the chart palette restoration spec
  (`docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md`):
  arc in chart amber (palette.BRASS = #f5d020), reticle in red
  (palette.RED = #e62a18), HUD chip in Orbitron + VT323. Palette + fonts
  pulled from `palette.py` and emitted as CSS custom properties on the
  layer-course root so a downstream theme can override without forking
  this module.
- Coordinate system: the chart's viewBox is centered on the chart origin
  via `viewBox="-half -half size size"` (see `render.render_chart`), so
  overlay coordinates can be emitted directly in the same chart-origin
  frame without an additional transform group.
- Reticle: outer dashed ring + inner solid ring + N/E/S/W ticks,
  matching the chart's existing reticle vocabulary.
- HUD chip: anchored to the Bezier peak P(t=0.5) and offset perpendicular
  to the tangent at that point on the OPPOSITE side from the bulge —
  guarantees chip + arc don't crowd the same region.
- HUD chip width is left to a placeholder fallback (148px). A small
  client-side post-render pass measures `<text>` getBBox() and resizes
  the surrounding `<rect>`. The emitted markup carries
  `data-course-chip="auto"` and a sentinel `data-text-id` on each text
  node so the client can find them. Client JS lives in sidequest-ui
  (separate follow-up).

Drop conditions return the input SVG unchanged. Each drop emits a
structured `drop_reason` enum so OTEL queries can split the failure
modes — see `_resolve_drop_reason`.
"""

from __future__ import annotations

import math
from typing import Literal

from sidequest.orbital import palette
from sidequest.orbital.course import PlottedCourse
from sidequest.orbital.course_geometry import bezier_control_offset, prograde_sign
from sidequest.orbital.models import OrbitsConfig
from sidequest.orbital.position import kepler_position

# ---------------------------------------------------------------------------
# Style tokens. Pulled from palette.py so a single edit to the chart palette
# ripples through both registers. The constants below are *fallback* values
# surfaced as CSS custom properties so a host page can override without
# re-rendering server-side.
# ---------------------------------------------------------------------------

COURSE_STROKE_COLOR = palette.BRASS
COURSE_RETICLE_COLOR = palette.RED
COURSE_CHIP_BG = palette.BG
COURSE_CHIP_BG_OPACITY = 0.85
COURSE_CHIP_STROKE_OPACITY = 0.6

_COURSE_STROKE_WIDTH = 1.6
# 8-on/6-off/2-on/6-off — Morse-flavoured, distinguishes the course arc
# from solid orbit ring engravings and from outer-system dashed rings.
_COURSE_DASH = "8 6 2 6"

# Reticle radii — shared vocabulary lives in palette.py per AC #16 of the
# orrery-v2 spec. Star reticle (palette.STAR_RETICLE_*) and course reticle
# share the dash pattern + name family, but the radii differ (course is
# smaller because course-target bodies are far from chart center).
_RETICLE_OUTER_R = palette.COURSE_RETICLE_OUTER_R
_RETICLE_INNER_R = palette.COURSE_RETICLE_INNER_R
_RETICLE_TICK_INNER = palette.COURSE_RETICLE_TICK_INNER
_RETICLE_TICK_OUTER = palette.COURSE_RETICLE_TICK_OUTER

_CHIP_LABEL_FONT_SIZE = 11
_CHIP_DETAIL_FONT_SIZE = 13
_CHIP_PADDING_X = 10
_CHIP_PADDING_Y = 6
_CHIP_LINE_HEIGHT = 17
_CHIP_PERP_OFFSET = 24.0
# Fallback width — replaced by client-side getBBox() pass when JS is loaded.
_CHIP_FALLBACK_HALF_WIDTH = 74


DropReason = Literal[
    "none_course",
    "unknown_party",
    "unknown_destination",
    "root_party",
]


# ---------------------------------------------------------------------------
# Geometry helpers (mirror render.py exactly so overlay coords align)
# ---------------------------------------------------------------------------


def _au_to_px_scale(orbits: OrbitsConfig, center_id: str) -> float:
    """Mirror of `render._viewport_for_scope` scale computation."""
    children = [b for b in orbits.bodies.values() if b.parent == center_id]
    max_au = max((c.semi_major_au or 0.0 for c in children), default=1.0) or 1.0
    size_px = 800
    half = size_px // 2
    pad = 1.2
    return (half / pad) / max_au


def _polar_to_xy(r_au: float, theta_deg: float, scale: float) -> tuple[float, float]:
    """Mirrors `render._polar_to_cartesian` exactly: x=r·s·cosθ, y=-r·s·sinθ."""
    rad = math.radians(theta_deg)
    return r_au * scale * math.cos(rad), -r_au * scale * math.sin(rad)


def _body_xy(
    orbits: OrbitsConfig, body_id: str, t_hours: float, scale: float
) -> tuple[float, float] | None:
    body = orbits.bodies.get(body_id)
    if body is None:
        return None
    if body.parent is None:
        return (0.0, 0.0)
    r_au, theta_deg = kepler_position(body, t_hours)
    return _polar_to_xy(r_au, theta_deg, scale)


def _bezier_control_points(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    party_theta_deg: float,
    dest_theta_deg: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Cubic Bezier control points — bulge perpendicular to chord, prograde."""
    chord_dx = x2 - x1
    chord_dy = y2 - y1
    chord_len = math.hypot(chord_dx, chord_dy)

    if chord_len < 1e-6:
        return (x1, y1), (x2, y2)

    # Unit perpendicular to chord (rotated 90° CCW).
    perp_x = -chord_dy / chord_len
    perp_y = chord_dx / chord_len

    prograde = prograde_sign(party_theta_deg, dest_theta_deg)
    offset = bezier_control_offset(chord_len, prograde)

    cp1 = (x1 + chord_dx / 3.0 + perp_x * offset, y1 + chord_dy / 3.0 + perp_y * offset)
    cp2 = (x2 - chord_dx / 3.0 + perp_x * offset, y2 - chord_dy / 3.0 + perp_y * offset)
    return cp1, cp2


def _bezier_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    """Cubic Bezier B(t) = (1-t)³P0 + 3(1-t)²t·P1 + 3(1-t)t²·P2 + t³·P3."""
    u = 1.0 - t
    b0 = u * u * u
    b1 = 3 * u * u * t
    b2 = 3 * u * t * t
    b3 = t * t * t
    return (
        b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0],
        b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1],
    )


def _bezier_tangent(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    """Cubic Bezier B'(t) = 3[(1-t)²(P1-P0) + 2(1-t)t(P2-P1) + t²(P3-P2)]."""
    u = 1.0 - t
    return (
        3 * (u * u * (p1[0] - p0[0]) + 2 * u * t * (p2[0] - p1[0]) + t * t * (p3[0] - p2[0])),
        3 * (u * u * (p1[1] - p0[1]) + 2 * u * t * (p2[1] - p1[1]) + t * t * (p3[1] - p2[1])),
    )


def _fmt(v: float, digits: int = 2) -> str:
    """Float to fixed-digits, trailing zeros stripped — keeps SVG terse."""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return s or "0"


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _format_dv(dv: float) -> str:
    """Format Δv: integer when whole, one decimal otherwise.

    Replaces the previous identical-branches bug. The integer fast-path is
    cosmetic so "Δv 4" reads cleaner than "Δv 4.0" on a HUD chip.
    """
    if dv == int(dv):
        return str(int(dv))
    return f"{dv:.1f}"


# ---------------------------------------------------------------------------
# SVG fragment composers
# ---------------------------------------------------------------------------


def _arc_path(
    p0: tuple[float, float],
    cp1: tuple[float, float],
    cp2: tuple[float, float],
    p1: tuple[float, float],
) -> str:
    """Bezier arc — dark underlay + dashed amber stroke on top.

    The underlay keeps the arc readable where it crosses the engraved
    orbit lattice.
    """
    d = (
        f"M {_fmt(p0[0])} {_fmt(p0[1])} "
        f"C {_fmt(cp1[0])} {_fmt(cp1[1])} "
        f"{_fmt(cp2[0])} {_fmt(cp2[1])} "
        f"{_fmt(p1[0])} {_fmt(p1[1])}"
    )
    underlay = (
        f'<path d="{d}" fill="none" stroke="{palette.BG}" '
        f'stroke-width="{_COURSE_STROKE_WIDTH + 4.0}" stroke-opacity="0.85" '
        f'stroke-linecap="round" />'
    )
    arc = (
        f'<path d="{d}" fill="none" stroke="var(--course-stroke)" '
        f'stroke-width="{_COURSE_STROKE_WIDTH}" '
        f'stroke-dasharray="{_COURSE_DASH}" '
        f'stroke-linecap="round" stroke-linejoin="round" />'
    )
    return underlay + arc


def _origin_tick(x: float, y: float) -> str:
    """Small open square at the party position — anchors the arc's start."""
    return (
        f'<g transform="translate({_fmt(x)},{_fmt(y)})">'
        f'<rect x="-3.5" y="-3.5" width="7" height="7" '
        f'fill="none" stroke="var(--course-stroke)" stroke-width="0.9" />'
        f"</g>"
    )


def _reticle(x: float, y: float) -> str:
    """Target reticle: outer dashed ring + inner solid ring + 4 ticks + dot."""
    inner = _RETICLE_INNER_R
    outer = _RETICLE_OUTER_R
    tick_in = _RETICLE_TICK_INNER
    tick_out = _RETICLE_TICK_OUTER
    return (
        f'<g id="course-target" transform="translate({_fmt(x)},{_fmt(y)})">'
        f'<circle cx="0" cy="0" r="{outer}" fill="none" '
        f'stroke="var(--course-reticle)" stroke-width="1.2" stroke-dasharray="3 2" />'
        f'<circle cx="0" cy="0" r="{inner}" fill="none" '
        f'stroke="var(--course-reticle)" stroke-width="1.4" />'
        f'<g stroke="var(--course-reticle)" stroke-width="1.6" stroke-linecap="square">'
        f'<line x1="0" y1="-{tick_out}" x2="0" y2="-{tick_in}" />'
        f'<line x1="0" y1="{tick_in}" x2="0" y2="{tick_out}" />'
        f'<line x1="-{tick_out}" y1="0" x2="-{tick_in}" y2="0" />'
        f'<line x1="{tick_in}" y1="0" x2="{tick_out}" y2="0" />'
        f"</g>"
        f'<circle cx="0" cy="0" r="1.2" fill="var(--course-reticle)" />'
        f"</g>"
    )


def _hud_chip(
    *,
    peak_x: float,
    peak_y: float,
    tangent: tuple[float, float],
    bulge_perp: tuple[float, float],
    label: str,
    detail: str,
) -> str:
    """HUD chip anchored to Bezier peak, offset opposite the bulge.

    Width is a placeholder (`_CHIP_FALLBACK_HALF_WIDTH * 2`); the client-side
    getBBox() pass measures the rendered text and resizes the rect + brackets.
    Sentinel attrs:
      data-course-chip="auto"          — chip group root, signals "needs sizing"
      data-course-chip-rect=""         — the rect to resize
      data-course-chip-brackets=""     — the corner-bracket group to redraw
      data-text-id="label" / "detail"  — text nodes the client measures
    """
    tx, ty = tangent
    tlen = math.hypot(tx, ty) or 1.0
    perp = (-ty / tlen, tx / tlen)
    if perp[0] * bulge_perp[0] + perp[1] * bulge_perp[1] > 0:
        perp = (-perp[0], -perp[1])

    chip_x = peak_x + perp[0] * _CHIP_PERP_OFFSET
    chip_y = peak_y + perp[1] * _CHIP_PERP_OFFSET

    chip_h = _CHIP_LINE_HEIGHT * 2 + _CHIP_PADDING_Y * 2
    rect_y = -_CHIP_PADDING_Y - 2

    half_w = _CHIP_FALLBACK_HALF_WIDTH
    full_w = half_w * 2

    tether_x = -perp[0] * _CHIP_PERP_OFFSET
    tether_y = -perp[1] * _CHIP_PERP_OFFSET
    tether_end = (tether_x * 0.4, tether_y * 0.4)

    label_y = _CHIP_PADDING_Y + _CHIP_LABEL_FONT_SIZE - 2
    detail_y = label_y + _CHIP_LINE_HEIGHT

    return (
        f'<g id="course-hud-chip" data-course-chip="auto" '
        f'transform="translate({_fmt(chip_x)},{_fmt(chip_y)})">'
        # Tether from peak → chip
        f'<line x1="{_fmt(tether_x)}" y1="{_fmt(tether_y)}" '
        f'x2="{_fmt(tether_end[0])}" y2="{_fmt(tether_end[1])}" '
        f'stroke="var(--course-stroke)" stroke-width="0.7" stroke-dasharray="2 2" />'
        # Chip body (placeholder width — client JS overwrites after measure)
        f'<rect data-course-chip-rect="" '
        f'x="-{half_w}" y="{rect_y}" width="{full_w}" height="{chip_h}" '
        f'fill="var(--course-chip-bg)" fill-opacity="{COURSE_CHIP_BG_OPACITY}" '
        f'stroke="var(--course-chip-stroke)" '
        f'stroke-opacity="{COURSE_CHIP_STROKE_OPACITY}" stroke-width="0.6" />'
        # Corner brackets (chart vocabulary cue)
        f'<g data-course-chip-brackets="" '
        f'stroke="var(--course-chip-stroke)" '
        f'stroke-opacity="{COURSE_CHIP_STROKE_OPACITY}" '
        f'stroke-width="0.9" fill="none">'
        f'<polyline points="-{half_w},{rect_y + 5} -{half_w},{rect_y} '
        f'-{half_w - 5},{rect_y}" />'
        f'<polyline points="{half_w},{rect_y + 5} {half_w},{rect_y} '
        f'{half_w - 5},{rect_y}" />'
        f'<polyline points="-{half_w},{rect_y + chip_h - 5} '
        f'-{half_w},{rect_y + chip_h} -{half_w - 5},{rect_y + chip_h}" />'
        f'<polyline points="{half_w},{rect_y + chip_h - 5} '
        f'{half_w},{rect_y + chip_h} {half_w - 5},{rect_y + chip_h}" />'
        f"</g>"
        # Label (Orbitron)
        f'<text data-text-id="label" x="0" y="{label_y}" text-anchor="middle" '
        f'font-family="{palette.FONT_DISPLAY}" font-weight="600" '
        f'font-size="{_CHIP_LABEL_FONT_SIZE}" letter-spacing="2" '
        f'fill="var(--course-stroke)">{_xml_escape(label)}</text>'
        # Detail (VT323)
        f'<text data-text-id="detail" x="0" y="{detail_y}" text-anchor="middle" '
        f'font-family="{palette.FONT_NUMERIC}" font-size="{_CHIP_DETAIL_FONT_SIZE}" '
        f'letter-spacing="1" fill="var(--course-stroke)" opacity="0.95">'
        f"{_xml_escape(detail)}</text>"
        f"</g>"
    )


# ---------------------------------------------------------------------------
# Drop-reason resolver — exported so intent.py and OTEL emit the same enum
# the renderer used to drop. Avoids drift between three places that all
# need to know "did we draw, and if not, why."
# ---------------------------------------------------------------------------


def _resolve_drop_reason(
    *,
    course: PlottedCourse | None,
    orbits: OrbitsConfig,
    party_body_id: str | None,
) -> DropReason | None:
    """Return a drop-reason string if the overlay must be skipped, else None.

    Order matters — first match wins. ``root_party`` is checked before
    ``unknown_destination`` so a root party with a bad destination is
    still reported as a root-party problem (the bigger, fixable cause).
    """
    if course is None:
        return "none_course"
    if party_body_id is None or party_body_id not in orbits.bodies:
        return "unknown_party"
    party = orbits.bodies[party_body_id]
    if party.parent is None:
        # Root body has no orbital position — overlay would draw from origin.
        return "root_party"
    if course.to_body_id not in orbits.bodies:
        return "unknown_destination"
    return None


# ---------------------------------------------------------------------------
# Top-level overlay composer
# ---------------------------------------------------------------------------


def _course_overlay_svg(
    *,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    party_theta_deg: float,
    dest_theta_deg: float,
    course: PlottedCourse,
) -> str:
    """Build the raw SVG fragment (no outer <svg> wrapper)."""
    p0 = (x1, y1)
    p1 = (x2, y2)
    cp1, cp2 = _bezier_control_points(x1, y1, x2, y2, party_theta_deg, dest_theta_deg)

    # Anchor chip at Bezier peak (t=0.5), offset perpendicular to the tangent
    # at that point, on the opposite side from the bulge.
    peak = _bezier_point(p0, cp1, cp2, p1, 0.5)
    tangent = _bezier_tangent(p0, cp1, cp2, p1, 0.5)

    # Chord-perpendicular × prograde sign tells us which side the arc bulged.
    chord_dx = x2 - x1
    chord_dy = y2 - y1
    chord_len = math.hypot(chord_dx, chord_dy) or 1.0
    chord_perp = (-chord_dy / chord_len, chord_dx / chord_len)
    prograde = prograde_sign(party_theta_deg, dest_theta_deg)
    bulge_perp = (chord_perp[0] * prograde, chord_perp[1] * prograde)

    label = (course.label or course.to_body_id).upper()
    eta_h = int(round(course.eta_hours))
    detail = f"ETA {eta_h}h · Δv {_format_dv(course.delta_v)}"

    arc = _arc_path(p0, cp1, cp2, p1)
    origin = _origin_tick(x1, y1)
    target = _reticle(x2, y2)
    chip = _hud_chip(
        peak_x=peak[0],
        peak_y=peak[1],
        tangent=tangent,
        bulge_perp=bulge_perp,
        label=label,
        detail=detail,
    )

    return arc + origin + target + chip


def _layer_open_tag() -> str:
    """Open tag for the course layer — surfaces theme tokens as CSS variables."""
    style = (
        f"--course-stroke: {COURSE_STROKE_COLOR};"
        f"--course-reticle: {COURSE_RETICLE_COLOR};"
        f"--course-chip-bg: {COURSE_CHIP_BG};"
        f"--course-chip-stroke: {COURSE_STROKE_COLOR};"
    )
    return f'<g id="layer-course" style="{style}">'


def render_course_overlay(
    *,
    chart_svg: str,
    course: PlottedCourse | None,
    orbits: OrbitsConfig,
    party_body_id: str | None,
    t_hours: float,
) -> str:
    """Compose the course Bezier overlay onto an existing chart SVG string.

    Returns the input SVG unchanged when a drop condition is met. Drop
    reasons are surfaced via the OTEL `course.render_overlay` span (caller
    in `intent.py`); ``_resolve_drop_reason`` is exported so the caller
    reads the same enum without duplicating logic.
    """
    drop = _resolve_drop_reason(course=course, orbits=orbits, party_body_id=party_body_id)
    if drop is not None:
        return chart_svg

    # Type-narrowing: drop=None implies all of these are non-None / valid.
    assert course is not None
    assert party_body_id is not None

    roots = [bid for bid, b in orbits.bodies.items() if b.parent is None]
    if not roots:
        return chart_svg
    center_id = roots[0]

    scale = _au_to_px_scale(orbits, center_id)

    party_xy = _body_xy(orbits, party_body_id, t_hours, scale)
    dest_xy = _body_xy(orbits, course.to_body_id, t_hours, scale)
    if party_xy is None or dest_xy is None:
        return chart_svg

    party_body = orbits.bodies[party_body_id]
    dest_body = orbits.bodies[course.to_body_id]
    _, party_theta = kepler_position(party_body, t_hours)
    _, dest_theta = kepler_position(dest_body, t_hours)

    overlay = _course_overlay_svg(
        x1=party_xy[0],
        y1=party_xy[1],
        x2=dest_xy[0],
        y2=dest_xy[1],
        party_theta_deg=party_theta,
        dest_theta_deg=dest_theta,
        course=course,
    )

    layer = f"{_layer_open_tag()}{overlay}</g>"
    close_tag = "</svg>"
    if close_tag in chart_svg:
        return chart_svg.replace(close_tag, layer + close_tag, 1)
    return chart_svg + layer
