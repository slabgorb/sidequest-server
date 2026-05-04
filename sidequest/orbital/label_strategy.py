"""ADR-094 — orrery body-label strategy selection and gutter flow-layout.

Pure logic module: no svgwrite imports, no SVG side effects. Consumed by
sidequest.orbital.render at the engraved-layer label cut point and at
the moon-band carve-out.

Three strategies per ADR-094:
  - textpath: label wraps along an SVG path (orbit ring or moon ring).
  - radial: label sits along the bearing ray from chart center to body.
  - callout: anchor mark + leader line + label block in the gutter zone.

Selection is rule-priority based; see select_label_strategies() for the
decision tree. Gutter layout is flow-packed by anchor bearing.

§9 deviation note in the implementation spec: forced_moon_band
generalizes ADR-094's narrow forced_companion rule. Any moon-band-rendered
body with a non-empty label is forced to callout regardless of parent type
— the structural reason is sub-pixel render position, not parent type.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class LabelStrategy(StrEnum):
    TEXTPATH = "textpath"
    RADIAL = "radial"
    CALLOUT = "callout"


class SelectionReason(StrEnum):
    FORCED_MOON_BAND = "forced_moon_band"
    EXPLICIT_CALLOUT_LABEL = "explicit_callout_label"
    TEXTPATH_FITS = "textpath_fits"
    RADIAL_FITS = "radial_fits"
    FALLBACK_TEXTPATH_TOO_SHORT = "fallback_textpath_too_short"
    FALLBACK_ARC_TOO_SHORT = "fallback_arc_too_short"
    FALLBACK_TIER_CAPPED = "fallback_tier_capped"


Register = Literal["engraved", "chalk", "prose"]


@dataclass(frozen=True)
class LabelDecision:
    body_id: str
    parent_id: str | None
    parent_type: str | None
    strategy: LabelStrategy
    reason: SelectionReason
    text: str
    register: Register
    text_width_px: float
    radial_tier: int | None              # RADIAL only
    arc_available_px: float | None       # RADIAL only
    textpath_path_id: str | None         # TEXTPATH only
    path_circumference_px: float | None  # TEXTPATH only
    callout_tag: str | None              # CALLOUT optional second line


@dataclass(frozen=True)
class CalloutBlock:
    """A single callout slot — singleton body or sibling group."""
    anchor_x: float
    anchor_y: float
    anchor_bearing_deg: float
    side: Literal["right", "left", "inset"]
    parent_label: str | None             # set when this is a grouped block
    members: tuple[LabelDecision, ...]   # 1+ decisions; >1 ⇒ grouped block
    block_x: float
    block_y: float
    block_width_px: float
    block_height_px: float


@dataclass(frozen=True)
class GutterLayout:
    blocks: tuple[CalloutBlock, ...]
    inset_fallback_count: int
    cross_group_crossing_count: int


from sidequest.orbital import palette  # noqa: E402  (kept near consumers)


def estimate_text_width_px(text: str, register: Register) -> float:
    """Upper-bound width estimate using calibrated palette constants.

    Bias: overestimate is the safe failure direction (forces callout
    instead of letting a tight radial overlap). Calibrated against
    UI-rendered bbox at the register's standard font size.
    """
    if register == "engraved":
        char_width = palette.LABEL_ENGRAVED_CHAR_WIDTH_PX
    elif register == "chalk":
        char_width = palette.LABEL_CHALK_CHAR_WIDTH_PX
    elif register == "prose":
        char_width = palette.LABEL_PROSE_CHAR_WIDTH_PX
    else:
        raise ValueError(f"unknown register: {register!r}")
    return float(len(text)) * char_width


@dataclass(frozen=True)
class _StrategyInput:
    """Per-body context the rule functions consume.

    Built once per render by the driver from orbits/chart/placements.
    Pure data: rule functions don't need the renderer.
    """
    body_id: str
    parent_id: str | None
    parent_type: str | None       # str rather than BodyType to avoid orbital→models→here cycle
    text: str                     # the label text (already stripped)
    register: Register
    text_width_px: float
    is_moon_band_child: bool      # body is rendered inside a moon band
    callout_label_annotation: object | None   # Annotation if explicit override exists, else None
    textpath_path_id: str | None  # set if engraved_label with curve_along resolves to this body
    path_circumference_px: float | None  # resolved path length when textpath_path_id set
    arc_to_neighbor_px: float | None     # smallest applicable arc to a peer's label edge
    radial_tier: int              # tier from existing _assign_collision_tiers (0..LABEL_TIER_MAX+1)
    # Anchor data (used by SVG handler later, threaded through unchanged):
    anchor_x: float
    anchor_y: float
    anchor_bearing_deg: float
    callout_tag: str | None       # for explicit callout_label, the tag line


def _rule_forced_moon_band(inp: _StrategyInput) -> LabelDecision | None:
    """If body is moon-band-rendered, force callout regardless of parent type.

    See spec §9 deviation: generalizes ADR-094's narrow forced_companion to
    cover any moon-band child with a label. Structural reason: sub-pixel
    render position has no radial space.
    """
    if not inp.is_moon_band_child:
        return None
    return LabelDecision(
        body_id=inp.body_id,
        parent_id=inp.parent_id,
        parent_type=inp.parent_type,
        strategy=LabelStrategy.CALLOUT,
        reason=SelectionReason.FORCED_MOON_BAND,
        text=inp.text,
        register=inp.register,
        text_width_px=inp.text_width_px,
        radial_tier=None,
        arc_available_px=None,
        textpath_path_id=None,
        path_circumference_px=None,
        callout_tag=inp.callout_tag,
    )


def _rule_explicit_callout_label(inp: _StrategyInput) -> LabelDecision | None:
    """If a callout_label annotation references this body, force callout."""
    if inp.callout_label_annotation is None:
        return None
    return LabelDecision(
        body_id=inp.body_id,
        parent_id=inp.parent_id,
        parent_type=inp.parent_type,
        strategy=LabelStrategy.CALLOUT,
        reason=SelectionReason.EXPLICIT_CALLOUT_LABEL,
        text=inp.text,
        register=inp.register,
        text_width_px=inp.text_width_px,
        radial_tier=None,
        arc_available_px=None,
        textpath_path_id=None,
        path_circumference_px=None,
        callout_tag=inp.callout_tag,
    )


def _rule_textpath(
    inp: _StrategyInput,
) -> tuple[LabelDecision | None, SelectionReason | None]:
    """Rule 3 of the decision tree.

    Returns:
      (decision, None) — textpath fits; emit TEXTPATH decision.
      (None, FALLBACK_TEXTPATH_TOO_SHORT) — annotation present but path too
        short; caller falls through to callout (NOT to radial — designer
        opted into curved label).
      (None, None) — no textpath annotation present; rule does not apply.
    """
    if inp.textpath_path_id is None:
        return None, None
    assert inp.path_circumference_px is not None, (
        "textpath_path_id set but path_circumference_px not measured"
    )
    if inp.path_circumference_px >= inp.text_width_px * palette.TEXTPATH_FIT_SAFETY:
        decision = LabelDecision(
            body_id=inp.body_id,
            parent_id=inp.parent_id,
            parent_type=inp.parent_type,
            strategy=LabelStrategy.TEXTPATH,
            reason=SelectionReason.TEXTPATH_FITS,
            text=inp.text,
            register=inp.register,
            text_width_px=inp.text_width_px,
            radial_tier=None,
            arc_available_px=None,
            textpath_path_id=inp.textpath_path_id,
            path_circumference_px=inp.path_circumference_px,
            callout_tag=inp.callout_tag,
        )
        return decision, None
    return None, SelectionReason.FALLBACK_TEXTPATH_TOO_SHORT


def _rule_radial(
    inp: _StrategyInput,
) -> tuple[LabelDecision | None, SelectionReason | None]:
    """Rule 4 of the decision tree.

    Latent reason priority when falling through (per ADR-094 §4.1):
      ARC_TOO_SHORT > TIER_CAPPED. The arc check runs first.
    """
    if inp.arc_to_neighbor_px is None:
        return None, None
    if inp.arc_to_neighbor_px / palette.ARC_FIT_SAFETY < inp.text_width_px:
        return None, SelectionReason.FALLBACK_ARC_TOO_SHORT
    if inp.radial_tier > palette.LABEL_TIER_MAX:
        return None, SelectionReason.FALLBACK_TIER_CAPPED
    decision = LabelDecision(
        body_id=inp.body_id,
        parent_id=inp.parent_id,
        parent_type=inp.parent_type,
        strategy=LabelStrategy.RADIAL,
        reason=SelectionReason.RADIAL_FITS,
        text=inp.text,
        register=inp.register,
        text_width_px=inp.text_width_px,
        radial_tier=inp.radial_tier,
        arc_available_px=inp.arc_to_neighbor_px,
        textpath_path_id=None,
        path_circumference_px=None,
        callout_tag=inp.callout_tag,
    )
    return decision, None


def _apply_decision_tree(inp: _StrategyInput) -> LabelDecision:
    """Apply the four-rule decision tree per ADR-094 §Selection rule.

    Priority order (first match wins, AC-S8):
      1. forced_moon_band   (structural — sub-pixel position, no exceptions)
      2. explicit_callout_label  (designer override)
      3. textpath_fits      (curve_along annotation, path long enough)
      4. radial_fits        (label has space at body's bearing)
      5. fallback callout   (latent reason from rule 3 or 4)
    """
    if (d := _rule_forced_moon_band(inp)) is not None:
        return d
    if (d := _rule_explicit_callout_label(inp)) is not None:
        return d

    decision, textpath_latent = _rule_textpath(inp)
    if decision is not None:
        return decision

    # Spec §5.3 #1: if textpath was opted-into and fell through, fall to
    # callout *without* trying radial — preserves designer intent.
    if textpath_latent is None:
        decision, radial_latent = _rule_radial(inp)
        if decision is not None:
            return decision
    else:
        radial_latent = None

    # Fallback callout — pick the most-specific latent reason available.
    # Priority: TEXTPATH_TOO_SHORT > ARC_TOO_SHORT > TIER_CAPPED.
    if textpath_latent is not None:
        reason = textpath_latent
    elif radial_latent is not None:
        reason = radial_latent
    else:
        # Truly no data — use ARC_TOO_SHORT as the generic "no space" reason.
        reason = SelectionReason.FALLBACK_ARC_TOO_SHORT

    return LabelDecision(
        body_id=inp.body_id,
        parent_id=inp.parent_id,
        parent_type=inp.parent_type,
        strategy=LabelStrategy.CALLOUT,
        reason=reason,
        text=inp.text,
        register=inp.register,
        text_width_px=inp.text_width_px,
        radial_tier=None,
        arc_available_px=None,
        textpath_path_id=None,
        path_circumference_px=None,
        callout_tag=inp.callout_tag,
    )


def _group_callouts_by_parent(
    decisions: list[LabelDecision],
    semi_major_by_id: dict[str, float],
) -> list[tuple[LabelDecision, ...]]:
    """Group moon-band callout decisions by parent_id; ≥CALLOUT_GROUP_MIN_MEMBERS
    form a single grouped block, fewer remain as singletons. Within a group,
    members sort by semi_major_au ascending (innermost first, AC-G3).

    Per spec §AC-G1: grouping is restricted to *moon-band* siblings (decisions
    with `reason == FORCED_MOON_BAND`). Top-level callouts (explicit_callout_label
    overrides + radial-fallbacks) remain singletons even when they share a
    parent_id, because the chart's reading is "this body is highlighted
    individually," not "these children all live in one moon system."
    """
    by_parent: dict[str | None, list[LabelDecision]] = {}
    for d in decisions:
        if d.reason != SelectionReason.FORCED_MOON_BAND:
            continue
        by_parent.setdefault(d.parent_id, []).append(d)

    groups: list[tuple[LabelDecision, ...]] = []
    grouped_ids: set[str] = set()
    for parent_id, members in by_parent.items():
        if parent_id is not None and len(members) >= palette.CALLOUT_GROUP_MIN_MEMBERS:
            sorted_members = sorted(
                members,
                key=lambda d: semi_major_by_id.get(d.body_id, 0.0),
            )
            groups.append(tuple(sorted_members))
            grouped_ids.update(m.body_id for m in sorted_members)

    # Everything not in a moon-band group is a singleton.
    for d in decisions:
        if d.body_id in grouped_ids:
            continue
        groups.append((d,))
    return groups


def _block_height_px(
    members: tuple[LabelDecision, ...],
    *,
    is_grouped: bool,
) -> float:
    """Vertical height of a callout block in pixels."""
    h = 2.0 * palette.CALLOUT_BLOCK_PADDING_PX
    if is_grouped:
        h += palette.CALLOUT_GROUP_TITLE_HEIGHT_PX
        h += len(members) * palette.CALLOUT_BLOCK_LINE_HEIGHT_PX
    else:
        # Singleton — exactly one member; tag adds an extra line.
        assert len(members) == 1
        h += palette.CALLOUT_BLOCK_LINE_HEIGHT_PX
        if members[0].callout_tag is not None:
            h += palette.CALLOUT_BLOCK_TAG_LINE_HEIGHT_PX
    return h


def _side_for_bearing(bearing_deg: float) -> Literal["right", "left"]:
    """Right gutter for bearings 270°→90° (sweeping through 0°);
    left gutter for bearings 90°→270°. Boundary convention:
    bearing == 90° goes left, bearing == 270° goes right.
    """
    b = bearing_deg % 360.0
    if b >= 270.0 or b < 90.0:
        return "right"
    return "left"


def _segments_intersect(
    p1: tuple[float, float], p2: tuple[float, float],
    p3: tuple[float, float], p4: tuple[float, float],
) -> bool:
    """True if open segments p1-p2 and p3-p4 cross (excluding shared endpoints)."""
    if {p1, p2} & {p3, p4}:
        return False

    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])

    return ccw(p1, p3, p4) != ccw(p2, p3, p4) and ccw(p1, p2, p3) != ccw(p1, p2, p4)


def _leader_segments_for_block(b: CalloutBlock) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return the (anchor → label-block-edge) line segment endpoints.
    Approximation: straight line from anchor to block-center on near edge.
    Real renderer draws orthogonal with one bend; for crossing detection a
    straight approximation is sufficient — the eye reads the same crossings.
    """
    edge_x = b.block_x if b.anchor_x < b.block_x else b.block_x + b.block_width_px
    edge_y = b.block_y + b.block_height_px / 2.0
    return ((b.anchor_x, b.anchor_y), (edge_x, edge_y))


def _count_cross_group_crossings(blocks: list[CalloutBlock]) -> int:
    """Pairwise leader-segment crossings between distinct groups.
    Within-group crossings are forbidden by construction (bearing-sort);
    this counts cross-group crossings only.
    """
    segs = [(_leader_segments_for_block(b), id(b.members)) for b in blocks]
    n = 0
    for i in range(len(segs)):
        (p1, p2), gid_i = segs[i]
        for j in range(i + 1, len(segs)):
            (p3, p4), gid_j = segs[j]
            if gid_i == gid_j:
                continue
            if _segments_intersect(p1, p2, p3, p4):
                n += 1
    return n


def lay_out_gutter(
    *,
    decisions: list[LabelDecision],
    anchor_by_id: dict[str, tuple[float, float, float]],
    semi_major_by_id: dict[str, float],
    viewport,
) -> GutterLayout:
    """Group, side-assign, sort, and pack callout blocks. Pure function.

    `viewport` must expose chart_min_x/max_x/top_y/bottom_y, svg_min_x/max_x.
    `anchor_by_id` maps body_id -> (x, y, bearing_deg).
    """
    callout_decisions = [d for d in decisions if d.strategy == LabelStrategy.CALLOUT]
    if not callout_decisions:
        return GutterLayout(blocks=(), inset_fallback_count=0, cross_group_crossing_count=0)

    groups = _group_callouts_by_parent(callout_decisions, semi_major_by_id)

    annotated: list[
        tuple[Literal["right", "left"], float, tuple[LabelDecision, ...], float, float, float]
    ] = []
    for g in groups:
        first = g[0]
        ax, ay, abear = anchor_by_id[first.body_id]
        is_grouped = len(g) >= palette.CALLOUT_GROUP_MIN_MEMBERS
        height = _block_height_px(g, is_grouped=is_grouped)
        side = _side_for_bearing(abear)
        annotated.append((side, abear, g, ax, ay, height))

    # Sort by vertical screen-position (top-down). SVG y-down; bearing 90°
    # → top of chart (y < 0); bearing 270° → bottom (y > 0). Sort key
    # = -sin(bearing_rad), which is small (negative) at top and large
    # (positive) at bottom — matches the renderer's _polar_to_cartesian.
    def sort_key(item):
        _, bearing, *_ = item
        return -math.sin(math.radians(bearing))

    annotated.sort(key=sort_key)

    blocks: list[CalloutBlock] = []
    inset_count = 0

    chart_top = viewport.chart_top_y
    chart_bottom = viewport.chart_bottom_y
    gap = palette.CALLOUT_BLOCK_INTER_BLOCK_GAP_PX

    cursor: dict[str, float] = {"right": chart_top, "left": chart_top}
    block_x_for_side: dict[str, float] = {
        "right": viewport.chart_max_x + palette.GUTTER_INNER_MARGIN_PX,
        "left": viewport.chart_min_x - palette.GUTTER_INNER_MARGIN_PX - palette.GUTTER_WIDTH_PX,
    }
    block_width_px = palette.GUTTER_WIDTH_PX - 2 * palette.GUTTER_INNER_MARGIN_PX

    for side, bearing, members, ax, ay, height in annotated:
        primary_side: Literal["right", "left"] = side
        opposite: Literal["right", "left"] = "left" if side == "right" else "right"

        chosen_side: Literal["right", "left", "inset"] | None = None
        chosen_x = chosen_y = 0.0

        for candidate in (primary_side, opposite):
            if cursor[candidate] + height <= chart_bottom:
                chosen_side = candidate
                chosen_y = cursor[candidate]
                chosen_x = block_x_for_side[candidate]
                cursor[candidate] = chosen_y + height + gap
                break

        if chosen_side is None:
            chosen_side = "inset"
            chosen_x = -block_width_px / 2.0
            chosen_y = -height / 2.0 + (inset_count * (height + gap))
            inset_count += 1

        is_grouped = len(members) >= palette.CALLOUT_GROUP_MIN_MEMBERS
        parent_label = members[0].parent_id if is_grouped else None

        blocks.append(CalloutBlock(
            anchor_x=ax,
            anchor_y=ay,
            anchor_bearing_deg=bearing,
            side=chosen_side,
            parent_label=parent_label,
            members=members,
            block_x=chosen_x,
            block_y=chosen_y,
            block_width_px=block_width_px,
            block_height_px=height,
        ))

    crossings = _count_cross_group_crossings(blocks)
    return GutterLayout(
        blocks=tuple(blocks),
        inset_fallback_count=inset_count,
        cross_group_crossing_count=crossings,
    )


def select_label_strategies(
    *,
    inputs: list[_StrategyInput],
) -> list[LabelDecision]:
    """Run the decision tree per body. Pure function.

    OTEL emission of chart.label_strategy spans happens in the renderer
    after this returns (the renderer has the trace context). This keeps
    label_strategy.py side-effect-free and trivially testable.
    """
    return [_apply_decision_tree(inp) for inp in inputs]
