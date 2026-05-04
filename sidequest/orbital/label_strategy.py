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
