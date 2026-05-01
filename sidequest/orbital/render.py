"""Server-side SVG renderer for the orbital chart.

Per spec §6: renderer produces a complete SVG document per (world, scope,
t_hours, party_at, plot_state). Layers: engraved (orbits + bodies + scale +
bearings), flavor (chart.yaml annotations), party (current location), plot
(when active). Output is deterministic for fixed inputs — snapshot tests
pin canonical outputs in Task 13.

Position math is deliberately simple in this plan: circular orbits only,
theta = epoch_phase + 360 * t_days / period. Plan 2 (Track C) brings in
the full position() module that will be a drop-in replacement here.
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

from sidequest.orbital.models import (
    Annotation,
    BodyDef,
    BodyType,
    ChartConfig,
    OrbitsConfig,
)

# svgwrite's built-in validators reject `data-*` attributes that we use for
# click-routing on rendered bodies. Allow them by patching the name check.
_orig_is_valid_attribute = (
    svgwrite.validator2.Full11Validator.is_valid_svg_attribute
)


def _is_valid_or_data_attr(self, elementname, attributename):
    if attributename.startswith("data-"):
        return True
    return _orig_is_valid_attribute(self, elementname, attributename)


svgwrite.validator2.Full11Validator.is_valid_svg_attribute = _is_valid_or_data_attr
svgwrite.validator2.Tiny12Validator.is_valid_svg_attribute = _is_valid_or_data_attr


def _check_svg_attribute_value_with_data(self, elementname, attributename, value):
    if attributename.startswith("data-"):
        return
    return _orig_check_value(self, elementname, attributename, value)


_orig_check_value = (
    svgwrite.validator2.Full11Validator.check_svg_attribute_value
)
svgwrite.validator2.Full11Validator.check_svg_attribute_value = (
    _check_svg_attribute_value_with_data
)
svgwrite.validator2.Tiny12Validator.check_svg_attribute_value = (
    _check_svg_attribute_value_with_data
)


@dataclass(frozen=True)
class Scope:
    """Render scope — which body is centered."""

    center_body_id: str

    @classmethod
    def system_root(cls) -> Scope:
        return cls(center_body_id="<root>")


def _body_position_au_polar(
    body: BodyDef, t_hours: float
) -> tuple[float, float]:
    """Return (au, theta_deg) of a body relative to its parent at story-time t.

    Circular-orbit approximation. Plan 2 (Track C) replaces this with the
    full `position()` module that supports eccentric orbits.
    """
    if body.parent is None:
        return (0.0, 0.0)
    t_days = t_hours / 24.0
    assert body.semi_major_au is not None
    assert body.period_days is not None
    assert body.epoch_phase_deg is not None
    theta = (body.epoch_phase_deg + 360.0 * t_days / body.period_days) % 360.0
    return (body.semi_major_au, theta)


def _polar_to_cartesian(
    au: float, theta_deg: float, scale: float
) -> tuple[float, float]:
    """Convert polar (AU, deg) to SVG cartesian pixels.

    SVG y-axis grows downward; we flip so 0° is "right" (3 o'clock) and 90°
    is "up" (12 o'clock) per orrery convention.
    """
    rad = math.radians(theta_deg)
    x = au * scale * math.cos(rad)
    y = -au * scale * math.sin(rad)
    return (x, y)


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
        viewBox=(
            f"{-viewport.half} {-viewport.half} "
            f"{viewport.size_px} {viewport.size_px}"
        ),
        profile="tiny",
        debug=False,
    )
    dwg.add(
        dwg.rect(
            insert=(-viewport.half, -viewport.half),
            size=(viewport.size_px, viewport.size_px),
            fill="black",
        )
    )
    dwg.add(_render_engraved_layer(orbits, center_id, viewport, t_hours))
    dwg.add(_render_flavor_layer(chart, viewport))
    dwg.add(_render_party_layer(orbits, center_id, viewport, t_hours, party_at))
    return dwg.tostring()


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


def _render_engraved_layer(
    orbits: OrbitsConfig,
    center_id: str,
    vp: _Viewport,
    t_hours: float,
) -> svgwrite.container.Group:
    g = svgwrite.container.Group(id="layer-engraved")
    center = orbits.bodies[center_id]

    g.add(_body_glyph(center, x=0, y=0, body_id=center_id))
    if center.label:
        g.add(
            svgwrite.text.Text(
                center.label,
                insert=(0, -16),
                fill=center.label_color or "yellow",
                text_anchor="middle",
                font_family="monospace",
                font_size=14,
            )
        )

    for body_id, body in orbits.bodies.items():
        if body.parent != center_id:
            continue
        au, theta = _body_position_au_polar(body, t_hours)
        radius_px = au * vp.au_to_px
        g.add(
            _attach_body_id(
                svgwrite.shapes.Circle(
                    center=(0, 0),
                    r=radius_px,
                    fill="none",
                    stroke="yellow",
                    stroke_width=0.6,
                ),
                body_id,
            )
        )
        x, y = _polar_to_cartesian(au, theta, vp.au_to_px)
        g.add(_body_glyph(body, x=x, y=y, body_id=body_id))
        if body.label:
            g.add(
                svgwrite.text.Text(
                    body.label,
                    insert=(x + 8, y - 6),
                    fill=body.label_color or "yellow",
                    font_family="monospace",
                    font_size=10,
                )
            )

    if center.type == BodyType.STAR:
        for theta_deg in (0, 90, 180, 270):
            x, y = _polar_to_cartesian(au=0.10, theta_deg=theta_deg, scale=vp.au_to_px)
            g.add(
                svgwrite.text.Text(
                    f"{theta_deg:03d}°",
                    insert=(x, y),
                    fill="yellow",
                    text_anchor="middle",
                    font_family="monospace",
                    font_size=8,
                )
            )

    return g


def _body_glyph(
    body: BodyDef, *, x: float, y: float, body_id: str
) -> svgwrite.base.BaseElement:
    """Pick the right shape for a body type."""
    if body.type == BodyType.STAR:
        circle = svgwrite.shapes.Circle(
            center=(x, y), r=8, fill="red", stroke="red"
        )
    elif body.type == BodyType.COMPANION:
        circle = svgwrite.shapes.Circle(center=(x, y), r=4, fill="red")
    elif body.type == BodyType.ARC_BELT:
        circle = svgwrite.shapes.Circle(center=(x, y), r=2, fill="orange")
    else:
        circle = svgwrite.shapes.Circle(center=(x, y), r=3, fill="yellow")
    return _attach_body_id(circle, body_id)


def _render_flavor_layer(
    chart: ChartConfig, vp: _Viewport
) -> svgwrite.container.Group:
    g = svgwrite.container.Group(id="layer-flavor")
    for annot in chart.annotations:
        elem = _render_annotation(annot, vp)
        if elem is not None:
            g.add(elem)
    return g


def _render_annotation(
    annot: Annotation, vp: _Viewport
) -> svgwrite.base.BaseElement | None:
    if annot.kind == "engraved_label":
        if annot.text is None:
            return None
        return svgwrite.text.Text(
            annot.text,
            insert=(0, -vp.half + 30),
            fill="yellow",
            text_anchor="middle",
            font_family="monospace",
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
            svgwrite.text.Text(
                annot.text,
                insert=(x, y),
                fill="yellow",
                text_anchor="middle",
                font_family="monospace",
                font_size=20,
            )
        )
        if annot.caption:
            group.add(
                svgwrite.text.Text(
                    annot.caption,
                    insert=(x, y + 14),
                    fill="yellow",
                    text_anchor="middle",
                    font_family="monospace",
                    font_size=9,
                    font_style="italic",
                )
            )
        return group
    if annot.kind == "scale_ruler":
        if annot.label is None:
            return None
        return svgwrite.text.Text(
            annot.label,
            insert=(0, vp.half - 20),
            fill="yellow",
            text_anchor="middle",
            font_family="monospace",
            font_size=9,
        )
    return None


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
            stroke="white",
            stroke_width=1.0,
        )
    )
    marker.add(
        svgwrite.shapes.Line(
            start=(x + 5, y - 10),
            end=(x + 15, y - 10),
            stroke="white",
            stroke_width=1.0,
        )
    )
    marker.add(
        svgwrite.shapes.Line(
            start=(x + 10, y - 15),
            end=(x + 10, y - 5),
            stroke="white",
            stroke_width=1.0,
        )
    )
    marker.add(
        svgwrite.text.Text(
            "← party",
            insert=(x + 18, y - 8),
            fill="white",
            font_family="cursive, monospace",
            font_size=9,
            font_style="italic",
        )
    )
    g.add(marker)
    return g
