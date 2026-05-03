"""Compose the course Bezier overlay onto an existing chart SVG.

Pure function: takes the chart SVG string + course state, returns a
new SVG string with the overlay layer inserted just before the
closing </svg> tag.

Per the plot-a-course design: cubic Bezier from party position to
destination position, control points offset perpendicular to the
chord by 0.3 × chord_length in the prograde direction. Pale amber
stroke (#d9a766), dashed, with a small reticle glyph at the destination
and a HUD chip.

Drop conditions (course=None, party_body_id=None, unknown target) return
the input SVG unchanged — the caller (intent.py) emits OTEL attrs for
these cases.
"""

from __future__ import annotations

import math

from sidequest.orbital.course import PlottedCourse
from sidequest.orbital.course_geometry import bezier_control_offset, prograde_sign
from sidequest.orbital.models import OrbitsConfig
from sidequest.orbital.position import kepler_position

COURSE_STROKE_COLOR = "#d9a766"
"""Pale amber per design open-choice resolution. Defer to art-director
for final hex against the engraved register palette; this is the
recommended starting value."""

_COURSE_STROKE_WIDTH = 1.5
_COURSE_DASH = "4 3"
_RETICLE_RADIUS = 7.0
_CHIP_FONT_SIZE = 10
_CHIP_PADDING_X = 6
_CHIP_PADDING_Y = 4
_CHIP_HEIGHT = 18


def _au_to_px_scale(orbits: OrbitsConfig, center_id: str) -> float:
    """Mirror of render._viewport_for_scope scale computation.

    Mirrors the renderer exactly so overlay coords align with body glyphs.
    """
    children = [b for b in orbits.bodies.values() if b.parent == center_id]
    max_au = max((c.semi_major_au or 0.0 for c in children), default=1.0) or 1.0
    size_px = 800
    half = size_px // 2
    pad = 1.2
    return (half / pad) / max_au


def _polar_to_xy(r_au: float, theta_deg: float, scale: float) -> tuple[float, float]:
    """Convert Kepler (r, θ) polar coords to SVG cartesian pixels.

    Mirrors render._polar_to_cartesian exactly:
      x = r * scale * cos(θ)
      y = -r * scale * sin(θ)   # SVG y-flip: 90° = up
    """
    rad = math.radians(theta_deg)
    return r_au * scale * math.cos(rad), -r_au * scale * math.sin(rad)


def _body_xy(
    orbits: OrbitsConfig, body_id: str, t_hours: float, scale: float
) -> tuple[float, float] | None:
    """Return SVG (x, y) for a body id at story-time t.

    Returns None for root bodies (no parent → position is origin (0, 0)).
    Root bodies (stars) always sit at the chart origin so (0, 0) is correct,
    but we only need positions for non-root bodies in the overlay.
    """
    body = orbits.bodies.get(body_id)
    if body is None:
        return None
    if body.parent is None:
        # Root body sits at the SVG origin.
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
    """Compute the two cubic Bezier control points for the course arc.

    Uses course_geometry.bezier_control_offset (0.3 × chord × prograde_sign)
    to bulge the arc in the prograde direction, giving it an orbital-flavored
    curve.

    The perpendicular direction to the chord (x1,y1)→(x2,y2) is computed
    in 2-D, then the offset is applied in that direction.
    """
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

    # Place control points at 1/3 and 2/3 along chord, offset perpendicularly.
    cp1 = (x1 + chord_dx / 3.0 + perp_x * offset, y1 + chord_dy / 3.0 + perp_y * offset)
    cp2 = (x2 - chord_dx / 3.0 + perp_x * offset, y2 - chord_dy / 3.0 + perp_y * offset)
    return cp1, cp2


def _fmt(v: float, digits: int = 2) -> str:
    """Format a float to at most `digits` decimal places, stripping trailing zeros."""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return s


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
    """Build the raw SVG fragment (no outer <svg> wrapper).

    Returns three elements concatenated:
    1. <path> — the cubic Bezier arc
    2. <g> — the target reticle at (x2, y2)
    3. <g> — the HUD chip anchored near the arc midpoint
    """
    cp1, cp2 = _bezier_control_points(x1, y1, x2, y2, party_theta_deg, dest_theta_deg)

    # ---- Bezier path ----
    path_d = (
        f"M {_fmt(x1)} {_fmt(y1)} "
        f"C {_fmt(cp1[0])} {_fmt(cp1[1])} "
        f"{_fmt(cp2[0])} {_fmt(cp2[1])} "
        f"{_fmt(x2)} {_fmt(y2)}"
    )
    bezier = (
        f'<path d="{path_d}" '
        f'fill="none" '
        f'stroke="{COURSE_STROKE_COLOR}" '
        f'stroke-width="{_COURSE_STROKE_WIDTH}" '
        f'stroke-dasharray="{_COURSE_DASH}" '
        f'stroke-linecap="round" />'
    )

    # ---- Target reticle at destination ----
    r = _RETICLE_RADIUS
    x2s, y2s = _fmt(x2), _fmt(y2)
    # Circle + cross-hair lines
    reticle = (
        f'<g id="course-target">'
        f'<circle cx="{x2s}" cy="{y2s}" r="{r}" '
        f'fill="none" stroke="{COURSE_STROKE_COLOR}" stroke-width="1" />'
        f'<line x1="{_fmt(x2 - r * 0.6)}" y1="{y2s}" '
        f'x2="{_fmt(x2 + r * 0.6)}" y2="{y2s}" '
        f'stroke="{COURSE_STROKE_COLOR}" stroke-width="0.8" />'
        f'<line x1="{x2s}" y1="{_fmt(y2 - r * 0.6)}" '
        f'x2="{x2s}" y2="{_fmt(y2 + r * 0.6)}" '
        f'stroke="{COURSE_STROKE_COLOR}" stroke-width="0.8" />'
        f'</g>'
    )

    # ---- HUD chip ----
    label = (course.label or course.to_body_id).upper()
    eta_h = int(round(course.eta_hours))
    dv = course.delta_v
    dv_str = f"{dv:.1f}" if dv != int(dv) else f"{dv:.1f}"

    chip_text_1 = label
    chip_text_2 = f"ETA {eta_h}h  Δv {dv_str}"

    # Chip anchor: midpoint of chord, offset slightly above.
    mid_x = (x1 + x2) / 2.0
    mid_y = (y1 + y2) / 2.0 - 20.0

    chip_w = max(len(chip_text_2) * 6 + _CHIP_PADDING_X * 2, 90)
    chip_h = _CHIP_HEIGHT * 2 + _CHIP_PADDING_Y * 2

    rect_x = _fmt(mid_x - chip_w / 2)
    rect_y = _fmt(mid_y - chip_h / 2)

    chip = (
        f'<g id="course-hud-chip">'
        f'<rect x="{rect_x}" y="{rect_y}" '
        f'width="{chip_w}" height="{chip_h}" rx="3" '
        f'fill="#1a1a1a" fill-opacity="0.85" '
        f'stroke="{COURSE_STROKE_COLOR}" stroke-width="0.8" />'
        f'<text x="{_fmt(mid_x)}" y="{_fmt(mid_y - _CHIP_PADDING_Y)}" '
        f'text-anchor="middle" '
        f'font-family="monospace" font-size="{_CHIP_FONT_SIZE}" '
        f'fill="{COURSE_STROKE_COLOR}">{chip_text_1}</text>'
        f'<text x="{_fmt(mid_x)}" y="{_fmt(mid_y + _CHIP_HEIGHT - _CHIP_PADDING_Y)}" '
        f'text-anchor="middle" '
        f'font-family="monospace" font-size="{_CHIP_FONT_SIZE}" '
        f'fill="{COURSE_STROKE_COLOR}">{chip_text_2}</text>'
        f'</g>'
    )

    return bezier + reticle + chip


def render_course_overlay(
    *,
    chart_svg: str,
    course: PlottedCourse | None,
    orbits: OrbitsConfig,
    party_body_id: str | None,
    t_hours: float,
) -> str:
    """Compose the course Bezier overlay onto an existing chart SVG string.

    Returns the input SVG unchanged when any drop condition is met:
    - ``course`` is None
    - ``party_body_id`` is None or not in orbits
    - ``course.to_body_id`` is not in orbits

    The caller (``intent.py``) emits OTEL ``course.render_overlay`` with
    ``dropped_invalid_target=True`` when a drop occurs due to bad body ids.
    """
    if course is None:
        return chart_svg
    if party_body_id is None or party_body_id not in orbits.bodies:
        return chart_svg
    if course.to_body_id not in orbits.bodies:
        return chart_svg

    # Resolve the chart center.  For a system-root chart the star is the
    # center; we find it by looking for the parent-less body.
    roots = [bid for bid, b in orbits.bodies.items() if b.parent is None]
    if not roots:
        return chart_svg
    center_id = roots[0]

    scale = _au_to_px_scale(orbits, center_id)

    party_xy = _body_xy(orbits, party_body_id, t_hours, scale)
    dest_xy = _body_xy(orbits, course.to_body_id, t_hours, scale)

    if party_xy is None or dest_xy is None:
        return chart_svg

    # Phase angles for prograde direction — use kepler_position to get theta.
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

    # Inject the overlay group just before the closing </svg> tag.
    # svgwrite.Drawing.tostring() always ends with "</svg>" or "</svg>\n".
    # The group id "layer-course" is intentional so client JS can
    # find/remove it on re-render without parsing the full SVG.
    layer = f'<g id="layer-course">{overlay}</g>'
    close_tag = "</svg>"
    if close_tag in chart_svg:
        return chart_svg.replace(close_tag, layer + close_tag, 1)
    # Fallback: append. Should not happen with well-formed SVG.
    return chart_svg + layer
