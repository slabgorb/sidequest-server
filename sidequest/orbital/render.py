"""Server-side SVG renderer for the orbital chart.

Per spec §6: renderer produces a complete SVG document per (world, scope,
t_hours, party_at, plot_state). Layers: engraved (orbits + bodies + scale +
bearings), flavor (chart.yaml annotations), party (current location), plot
(when active). Output is deterministic for fixed inputs — snapshot tests
pin canonical outputs.

Position math is now Kepler-correct via `sidequest.orbital.position`.
For e=0 bodies the output is bit-identical to the prior circular formula,
so `eccentricity=0` fixtures don't drift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import svgwrite
import svgwrite.base
import svgwrite.container
import svgwrite.shapes
import svgwrite.text
import svgwrite.validator2

from sidequest.orbital import palette
from sidequest.orbital.models import (
    Annotation,
    BodyDef,
    BodyType,
    ChartConfig,
    OrbitsConfig,
)
from sidequest.orbital.position import ellipse_geometry, kepler_position
from sidequest.telemetry.spans.chart import emit_chart_render

# svgwrite's built-in validators reject attributes that aren't in their
# (somewhat outdated) allowlists. We need:
#   - `data-*` for click-routing on rendered bodies
#   - `paint-order` for the haloed-text trick (stroke-then-fill on a single
#     text element, used everywhere instead of feMorphology+feGaussianBlur
#     which the `tiny` profile rejects)
#   - `class` so we can mark drillable groups for client-side hover styling
_PASSTHROUGH_ATTRS = frozenset({"paint-order", "class"})

_orig_is_valid_attribute = svgwrite.validator2.Full11Validator.is_valid_svg_attribute


def _is_valid_or_passthrough(self, elementname, attributename):
    if attributename.startswith("data-"):
        return True
    if attributename in _PASSTHROUGH_ATTRS:
        return True
    return _orig_is_valid_attribute(self, elementname, attributename)


svgwrite.validator2.Full11Validator.is_valid_svg_attribute = _is_valid_or_passthrough
svgwrite.validator2.Tiny12Validator.is_valid_svg_attribute = _is_valid_or_passthrough


def _check_svg_attribute_value_passthrough(self, elementname, attributename, value):
    if attributename.startswith("data-"):
        return
    if attributename in _PASSTHROUGH_ATTRS:
        return
    return _orig_check_value(self, elementname, attributename, value)


_orig_check_value = svgwrite.validator2.Full11Validator.check_svg_attribute_value
svgwrite.validator2.Full11Validator.check_svg_attribute_value = (
    _check_svg_attribute_value_passthrough
)
svgwrite.validator2.Tiny12Validator.check_svg_attribute_value = (
    _check_svg_attribute_value_passthrough
)


@dataclass(frozen=True)
class Scope:
    """Render scope — which body is centered."""

    center_body_id: str

    @classmethod
    def system_root(cls) -> Scope:
        return cls(center_body_id="<root>")


def _body_position_au_polar(body: BodyDef, t_hours: float) -> tuple[float, float]:
    """Return (au, theta_deg) of a body relative to its parent at story-time t.

    Thin shim onto `position.kepler_position`. Kept as a private function
    for grep-locality — every "where is body X right now" question lands
    at the same call site, easy to OTEL-instrument later.
    """
    return kepler_position(body, t_hours)


def _polar_to_cartesian(au: float, theta_deg: float, scale: float) -> tuple[float, float]:
    """Convert polar (AU, deg) to SVG cartesian pixels.

    SVG y-axis grows downward; we flip so 0° is "right" (3 o'clock) and 90°
    is "up" (12 o'clock) per orrery convention.
    """
    rad = math.radians(theta_deg)
    x = au * scale * math.cos(rad)
    y = -au * scale * math.sin(rad)
    return (x, y)


def _haloed_text(
    content: str,
    *,
    insert: tuple[float, float],
    fill: str,
    font_family: str = palette.FONT_DISPLAY,
    font_size: int = 10,
    text_anchor: str = "start",
    font_style: str | None = None,
) -> svgwrite.text.Text:
    """Text with a dark halo via paint-order=stroke. Tiny-profile-safe.

    The stroke renders first (creating a silhouette outline), then the fill
    paints over it — leaving a halo of background color around the visible
    glyph. Cheaper than an feMorphology+feGaussianBlur filter (which the
    `tiny` profile rejects) and works at any zoom.
    """
    text = svgwrite.text.Text(
        content,
        insert=insert,
        fill=fill,
        font_family=font_family,
        font_size=font_size,
        text_anchor=text_anchor,
    )
    text["stroke"] = palette.BG
    text["stroke-width"] = 3
    text["stroke-linejoin"] = "round"
    text["paint-order"] = "stroke"
    if font_style is not None:
        text["font-style"] = font_style
    return text


def _star_glyph(x: float, y: float) -> svgwrite.container.Group:
    """Star: red disk with concentric corona halos. Tiny-safe (no gradients)."""
    g = svgwrite.container.Group()
    # Outer dim corona
    g.add(svgwrite.shapes.Circle(center=(x, y), r=18, fill=palette.RED, fill_opacity=0.12))
    # Inner bright corona
    g.add(svgwrite.shapes.Circle(center=(x, y), r=14, fill=palette.RED, fill_opacity=0.30))
    g.add(svgwrite.shapes.Circle(center=(x, y), r=10, fill=palette.RED))
    return g


def _habitat_glyph(x: float, y: float, *, fill: str) -> svgwrite.shapes.Polygon:
    """Habitat: brass diamond (square rotated 45°). Reads as 'man-made'."""
    pts = [(x, y - 5), (x + 5, y), (x, y + 5), (x - 5, y)]
    return svgwrite.shapes.Polygon(points=pts, fill=fill, stroke=palette.BRASS, stroke_width=1)


def _gate_glyph(x: float, y: float, *, fill: str) -> svgwrite.shapes.Polygon:
    """Gate: hexagon outline. Reads as 'infrastructure'."""
    # Pointy-top hexagon, r=6
    r = 6
    pts = [
        (x, y - r),
        (x + r * 0.866, y - r * 0.5),
        (x + r * 0.866, y + r * 0.5),
        (x, y + r),
        (x - r * 0.866, y + r * 0.5),
        (x - r * 0.866, y - r * 0.5),
    ]
    return svgwrite.shapes.Polygon(points=pts, fill=fill, stroke=palette.BRASS, stroke_width=1)


def _wreck_glyph(x: float, y: float) -> svgwrite.container.Group:
    """Wreck: jagged 5-point asterisk in dim brass. Reads as 'dead'."""
    g = svgwrite.container.Group()
    r = 5
    for i in range(5):
        theta = math.radians(90 + i * 72)
        x2 = x + r * math.cos(theta)
        y2 = y - r * math.sin(theta)
        g.add(svgwrite.shapes.Line(start=(x, y), end=(x2, y2), stroke=palette.DIM, stroke_width=1))
    return g


def _gas_giant_overlay(x: float, y: float, body_radius: float) -> svgwrite.container.Group:
    """Three horizontal banding lines across a body disk for gas-giant subtype."""
    g = svgwrite.container.Group()
    for dy in (-body_radius * 0.4, 0.0, body_radius * 0.4):
        g.add(
            svgwrite.shapes.Line(
                start=(x - body_radius * 0.9, y + dy),
                end=(x + body_radius * 0.9, y + dy),
                stroke=palette.BRASS,
                stroke_width=0.6,
                stroke_opacity=0.6,
            )
        )
    return g


def _dotted_arc(
    *,
    cx: float,
    cy: float,
    radius: float,
    from_deg: float,
    extent_deg: float,
    color: str,
    dot_spacing_px: float = 4.0,
) -> svgwrite.container.Group:
    """Arc rendered as a series of small dots. Tiny-profile-safe (no path
    stroke-dasharray dependence on path arc support)."""
    g = svgwrite.container.Group()
    # Number of dots so spacing ≈ dot_spacing_px along the arc circumference.
    arc_len_px = abs(extent_deg) * math.pi / 180.0 * radius
    n = max(2, int(arc_len_px / dot_spacing_px))
    for i in range(n):
        theta_deg = from_deg + extent_deg * (i / max(1, n - 1))
        rad = math.radians(theta_deg)
        x = cx + radius * math.cos(rad)
        y = cy - radius * math.sin(rad)
        g.add(svgwrite.shapes.Circle(center=(x, y), r=0.8, fill=color))
    return g


def render_chart(
    *,
    orbits: OrbitsConfig,
    chart: ChartConfig,
    scope: Scope,
    t_hours: float,
    party_at: str | None,
) -> str:
    """Compose the full chart SVG. Returns a UTF-8 string."""
    center_id = _resolve_scope_center(orbits, scope)
    viewport = _viewport_for_scope(orbits, center_id)

    dwg = svgwrite.Drawing(
        size=(viewport.size_px, viewport.size_px),
        viewBox=(f"{-viewport.half} {-viewport.half} {viewport.size_px} {viewport.size_px}"),
        profile="tiny",
        debug=False,
    )
    dwg.add(
        dwg.rect(
            insert=(-viewport.half, -viewport.half),
            size=(viewport.size_px, viewport.size_px),
            fill=palette.BG,
        )
    )
    dwg.add(_render_engraved_layer(orbits, center_id, viewport, t_hours))
    dwg.add(_render_flavor_layer(chart, viewport))
    dwg.add(_render_party_layer(orbits, center_id, viewport, t_hours, party_at))
    output = dwg.tostring()
    emit_chart_render(
        scope_center=center_id,
        t_hours=t_hours,
        party_at=party_at,
        body_count=len(orbits.bodies),
        output_size_bytes=len(output.encode("utf-8")),
    )
    return output


@dataclass(frozen=True)
class _Viewport:
    size_px: int
    half: int
    au_to_px: float


def _resolve_scope_center(orbits: OrbitsConfig, scope: Scope) -> str:
    if scope.center_body_id == "<root>":
        roots = [bid for bid, b in orbits.bodies.items() if b.parent is None]
        if len(roots) != 1:
            raise ValueError(
                f"system_root scope requires exactly one parent-less body; got {roots!r}"
            )
        return roots[0]
    if scope.center_body_id not in orbits.bodies:
        raise ValueError(f"scope center {scope.center_body_id!r} not in bodies")
    return scope.center_body_id


def _viewport_for_scope(orbits: OrbitsConfig, center_id: str) -> _Viewport:
    """Pick a viewport that fits the largest direct child orbit + 20% pad."""
    children = [b for b in orbits.bodies.values() if b.parent == center_id]
    max_au = max((c.semi_major_au or 0.0 for c in children), default=1.0) or 1.0
    size_px = 800
    half = size_px // 2
    pad = 1.2
    au_to_px = (half / pad) / max_au
    return _Viewport(size_px=size_px, half=half, au_to_px=au_to_px)


def _attach_body_id(elem: svgwrite.base.BaseElement, body_id: str) -> svgwrite.base.BaseElement:
    """Set data-body-id directly on attribs to bypass svgwrite's profile validator.

    svgwrite rejects `data-*` attributes under any built-in profile; setting
    via `attribs[...]` skips validation and emits the attribute verbatim.
    """
    elem.attribs["data-body-id"] = body_id
    return elem


def _drillable_body_ids(orbits: OrbitsConfig) -> set[str]:
    """Bodies that have at least one child — eligible for cluster-glyph drill-in."""
    return {bid for bid in orbits.bodies if any(b.parent == bid for b in orbits.bodies.values())}


def _render_engraved_layer(
    orbits: OrbitsConfig,
    center_id: str,
    vp: _Viewport,
    t_hours: float,
) -> svgwrite.container.Group:
    g = svgwrite.container.Group(id="layer-engraved")
    center = orbits.bodies[center_id]

    if center.parent is not None:
        if center.parent not in orbits.bodies:
            raise ValueError(f"center {center_id!r} has parent {center.parent!r} not in bodies")
        parent = orbits.bodies[center.parent]
        parent_label = parent.label or center.parent.upper()
        edge = svgwrite.container.Group()
        edge.attribs["data-action"] = "drill_out"
        edge.attribs["data-parent-id"] = center.parent
        edge.add(
            _haloed_text(
                f"← {parent_label} SYSTEM",
                insert=(-vp.half + 20, 0),
                fill=palette.BRASS,
                font_size=10,
            )
        )
        g.add(edge)

    g.add(_body_glyph(center, x=0, y=0, body_id=center_id))
    if center.label:
        g.add(
            _haloed_text(
                center.label,
                insert=(0, -22),
                fill=center.label_color or palette.BRASS,
                text_anchor="middle",
                font_size=14,
            )
        )

    drillable_ids = _drillable_body_ids(orbits)

    for body_id, body in orbits.bodies.items():
        if body.parent != center_id:
            continue

        # ARC_BELT renders as a dotted arc on the orbit radius itself —
        # not as a body glyph at one position, since a belt has no point.
        if body.type == BodyType.ARC_BELT:
            assert body.semi_major_au is not None
            assert body.epoch_phase_deg is not None
            assert body.arc_extent_deg is not None
            radius_px = body.semi_major_au * vp.au_to_px
            arc = _dotted_arc(
                cx=0,
                cy=0,
                radius=radius_px,
                from_deg=body.epoch_phase_deg,
                extent_deg=body.arc_extent_deg,
                color=palette.RED if body.hazard else palette.BRASS,
            )
            arc.attribs["data-body-id"] = body_id
            g.add(arc)
            if body.label:
                # Anchor the label at the arc midpoint.
                mid_deg = body.epoch_phase_deg + body.arc_extent_deg / 2
                mx, my = _polar_to_cartesian(body.semi_major_au, mid_deg, vp.au_to_px)
                g.add(
                    _haloed_text(
                        body.label,
                        insert=(mx + 8, my - 6),
                        fill=body.label_color or palette.BRASS,
                        font_size=10,
                    )
                )
            continue

        au, theta = _body_position_au_polar(body, t_hours)
        # Orbit ellipse: focus at parent (origin), center offset by -c=-a·e
        # along +x. For e=0 this reduces to a centered circle, so existing
        # circular-orbit fixtures don't drift.
        ell = ellipse_geometry(body, vp.au_to_px)
        g.add(
            _attach_body_id(
                svgwrite.shapes.Ellipse(
                    center=(ell.center_x_px, ell.center_y_px),
                    r=(ell.semi_major_px, ell.semi_minor_px),
                    fill="none",
                    stroke=palette.BRASS,
                    stroke_width=0.6,
                ),
                body_id,
            )
        )
        x, y = _polar_to_cartesian(au, theta, vp.au_to_px)
        if body_id in drillable_ids:
            cluster = svgwrite.container.Group()
            cluster.attribs["data-action"] = f"drill_in:{body_id}"
            cluster.attribs["data-body-id"] = body_id
            cluster.attribs["class"] = "drillable"
            cluster.add(
                svgwrite.shapes.Circle(
                    center=(x, y),
                    r=12,
                    fill="none",
                    stroke=palette.BRASS,
                    stroke_dasharray="2,2",
                    stroke_width=0.6,
                )
            )
            cluster.add(_body_glyph(body, x=x, y=y, body_id=body_id))
            child_count = sum(1 for c in orbits.bodies.values() if c.parent == body_id)
            cluster.add(
                _haloed_text(
                    f"+{child_count}",
                    insert=(x + 16, y + 4),
                    fill=palette.BRASS,
                    font_size=8,
                )
            )
            g.add(cluster)
        else:
            g.add(_body_glyph(body, x=x, y=y, body_id=body_id))
        if body.label:
            g.add(
                _haloed_text(
                    body.label,
                    insert=(x + 10, y - 8),
                    fill=body.label_color or palette.BRASS,
                    font_size=10,
                )
            )

    if center.type == BodyType.STAR:
        for theta_deg in (0, 90, 180, 270):
            x, y = _polar_to_cartesian(au=0.10, theta_deg=theta_deg, scale=vp.au_to_px)
            g.add(
                _haloed_text(
                    f"{theta_deg:03d}°",
                    insert=(x, y),
                    fill=palette.DIM,
                    text_anchor="middle",
                    font_family=palette.FONT_NUMERIC,
                    font_size=8,
                )
            )

    return g


def _body_glyph(body: BodyDef, *, x: float, y: float, body_id: str) -> svgwrite.base.BaseElement:
    """Pick the glyph for a body type. Honors hazard override + subtype.

    Hazard semantic: any body with `hazard: true` adopts RED fill regardless
    of type. Subtype semantic: `subtype="gas_giant"` overlays banding lines
    on a habitat-typed body.
    """
    fill = palette.RED if body.hazard else _glyph_default_fill(body.type)

    if body.type == BodyType.STAR:
        elem: svgwrite.base.BaseElement = _star_glyph(x, y)
    elif body.type == BodyType.COMPANION:
        elem = svgwrite.shapes.Circle(center=(x, y), r=6, fill=palette.RED)
    elif body.type == BodyType.HABITAT:
        if body.subtype == "gas_giant":
            # Gas giant: larger brass disk with banding overlay.
            group = svgwrite.container.Group()
            r = 10
            group.add(svgwrite.shapes.Circle(center=(x, y), r=r, fill=fill))
            group.add(_gas_giant_overlay(x, y, body_radius=r))
            elem = group
        else:
            elem = _habitat_glyph(x, y, fill=fill)
    elif body.type == BodyType.ARC_BELT:
        # Belt-as-glyph fallback (shouldn't be reached — engraved layer
        # special-cases ARC_BELT to render as a dotted arc on the orbit).
        elem = svgwrite.shapes.Circle(center=(x, y), r=2, fill=palette.BRASS)
    elif body.type == BodyType.GATE:
        elem = _gate_glyph(x, y, fill=fill)
    elif body.type == BodyType.WRECK:
        elem = _wreck_glyph(x, y)
    else:
        # Unknown BodyType — fail loud per CLAUDE.md "no silent fallbacks".
        raise ValueError(f"unknown BodyType for body {body_id!r}: {body.type!r}")

    return _attach_body_id(elem, body_id)


def _glyph_default_fill(body_type: BodyType) -> str:
    """Default (non-hazard) fill color for a body type."""
    if body_type in (BodyType.STAR, BodyType.COMPANION):
        return palette.RED
    if body_type == BodyType.WRECK:
        return palette.DIM
    return palette.BRASS


def _render_flavor_layer(chart: ChartConfig, vp: _Viewport) -> svgwrite.container.Group:
    g = svgwrite.container.Group(id="layer-flavor")
    for annot in chart.annotations:
        elem = _render_annotation(annot, vp)
        if elem is not None:
            g.add(elem)
    return g


def _render_annotation(annot: Annotation, vp: _Viewport) -> svgwrite.base.BaseElement | None:
    if annot.kind == "engraved_label":
        if annot.text is None:
            return None
        return _haloed_text(
            annot.text,
            insert=(0, -vp.half + 30),
            fill=palette.BRASS,
            text_anchor="middle",
            font_size=12,
            font_style="italic",
        )
    if annot.kind == "glyph":
        if annot.text is None or annot.at is None:
            return None
        ra = float(annot.at.get("ra_deg", 0))
        au = float(annot.at.get("au", 0))
        x, y = _polar_to_cartesian(au, ra, vp.au_to_px)
        group = svgwrite.container.Group()
        group.add(
            _haloed_text(
                annot.text,
                insert=(x, y),
                fill=palette.BRASS,
                text_anchor="middle",
                font_size=20,
            )
        )
        if annot.caption:
            group.add(
                _haloed_text(
                    annot.caption,
                    insert=(x, y + 14),
                    fill=palette.BRASS,
                    text_anchor="middle",
                    font_size=9,
                    font_style="italic",
                )
            )
        return group
    if annot.kind == "scale_ruler":
        if annot.label is None:
            return None
        return _haloed_text(
            annot.label,
            insert=(0, vp.half - 20),
            fill=palette.BRASS,
            text_anchor="middle",
            font_size=9,
        )
    if annot.kind == "bearing_marks":
        # 4 cardinal degree marks on a small radius near the chart center.
        # Subtler than the auto-emitted star bearings — these are explicit
        # author intent in chart.yaml.
        bearings = annot.bearings or [0.0, 90.0, 180.0, 270.0]
        group = svgwrite.container.Group()
        for theta_deg in bearings:
            x, y = _polar_to_cartesian(au=0.05, theta_deg=theta_deg, scale=vp.au_to_px)
            group.add(
                _haloed_text(
                    f"{int(theta_deg):03d}°",
                    insert=(x, y),
                    fill=palette.DIM,
                    text_anchor="middle",
                    font_family=palette.FONT_NUMERIC,
                    font_size=7,
                )
            )
        return group
    if annot.kind == "anomaly_marker":
        # Hexagon outlined in red with a single-character glyph (e.g. "Ψ").
        # Placement via `at: { ra_deg, au }`. Caption optional.
        if annot.at is None:
            return None
        ra = float(annot.at.get("ra_deg", 0))
        au = float(annot.at.get("au", 0))
        x, y = _polar_to_cartesian(au, ra, vp.au_to_px)
        r = 8
        pts = [
            (x, y - r),
            (x + r * 0.866, y - r * 0.5),
            (x + r * 0.866, y + r * 0.5),
            (x, y + r),
            (x - r * 0.866, y + r * 0.5),
            (x - r * 0.866, y - r * 0.5),
        ]
        group = svgwrite.container.Group()
        group.add(
            svgwrite.shapes.Polygon(
                points=pts, fill=palette.BG, stroke=palette.RED, stroke_width=1.2
            )
        )
        if annot.text:
            group.add(
                _haloed_text(
                    annot.text,
                    insert=(x, y + 4),
                    fill=palette.RED,
                    text_anchor="middle",
                    font_size=10,
                )
            )
        if annot.caption:
            group.add(
                _haloed_text(
                    annot.caption,
                    insert=(x, y + r + 12),
                    fill=palette.RED,
                    text_anchor="middle",
                    font_size=9,
                    font_style="italic",
                )
            )
        return group
    if annot.kind == "lagrange_point":
        # Small triangle with L1/L4/L5 label (label field carries the point id).
        if annot.at is None:
            return None
        ra = float(annot.at.get("ra_deg", 0))
        au = float(annot.at.get("au", 0))
        x, y = _polar_to_cartesian(au, ra, vp.au_to_px)
        r = 4
        pts = [(x, y - r), (x - r * 0.866, y + r * 0.5), (x + r * 0.866, y + r * 0.5)]
        group = svgwrite.container.Group()
        group.add(
            svgwrite.shapes.Polygon(
                points=pts, fill=palette.BG, stroke=palette.BRASS, stroke_width=0.8
            )
        )
        if annot.label:
            group.add(
                _haloed_text(
                    annot.label,
                    insert=(x + 6, y + 3),
                    fill=palette.BRASS,
                    font_family=palette.FONT_NUMERIC,
                    font_size=8,
                )
            )
        return group
    if annot.kind == "flight_corridor":
        # Dashed line between two body coordinates.
        # `at` carries: { from_ra_deg, from_au, to_ra_deg, to_au }
        if annot.at is None:
            return None
        try:
            x1, y1 = _polar_to_cartesian(
                float(annot.at["from_au"]),
                float(annot.at["from_ra_deg"]),
                vp.au_to_px,
            )
            x2, y2 = _polar_to_cartesian(
                float(annot.at["to_au"]),
                float(annot.at["to_ra_deg"]),
                vp.au_to_px,
            )
        except KeyError as e:
            raise ValueError(
                f"flight_corridor annotation requires from_ra_deg/from_au/"
                f"to_ra_deg/to_au in `at`; missing {e.args[0]!r}"
            ) from e
        line = svgwrite.shapes.Line(
            start=(x1, y1), end=(x2, y2), stroke=palette.DIM, stroke_width=0.8
        )
        line["stroke-dasharray"] = "4,4"
        return line
    # Unknown kind reached the renderer despite the model-level validator —
    # something bypassed it. Fail loud.
    raise ValueError(
        f"renderer has no handler for annotation kind {annot.kind!r}; "
        f"either the model validator is out of sync with KNOWN_ANNOTATION_KINDS "
        f"or this code is missing a branch."
    )


def _render_party_layer(
    orbits: OrbitsConfig,
    center_id: str,
    vp: _Viewport,
    t_hours: float,
    party_at: str | None,
) -> svgwrite.container.Group:
    g = svgwrite.container.Group(id="layer-party")
    if party_at is None or party_at not in orbits.bodies:
        return g
    body = orbits.bodies[party_at]
    if party_at == center_id:
        x, y = (0.0, 0.0)
    elif body.parent == center_id:
        au, theta = _body_position_au_polar(body, t_hours)
        x, y = _polar_to_cartesian(au, theta, vp.au_to_px)
    else:
        # Cross-scope: off-chart-edge indicator. Refined in Task 12.
        x, y = (vp.half - 16, 0.0)
    marker = svgwrite.container.Group()
    marker.attribs["data-party-at"] = party_at
    marker.add(
        svgwrite.shapes.Circle(
            center=(x + 10, y - 10),
            r=4,
            fill="none",
            stroke=palette.PARTY,
            stroke_width=1.0,
        )
    )
    marker.add(
        svgwrite.shapes.Line(
            start=(x + 5, y - 10),
            end=(x + 15, y - 10),
            stroke=palette.PARTY,
            stroke_width=1.0,
        )
    )
    marker.add(
        svgwrite.shapes.Line(
            start=(x + 10, y - 15),
            end=(x + 10, y - 5),
            stroke=palette.PARTY,
            stroke_width=1.0,
        )
    )
    marker.add(
        _haloed_text(
            "← party",
            insert=(x + 18, y - 8),
            fill=palette.PARTY,
            font_size=9,
            font_style="italic",
        )
    )
    g.add(marker)
    return g
