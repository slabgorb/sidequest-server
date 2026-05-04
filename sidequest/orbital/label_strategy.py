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
