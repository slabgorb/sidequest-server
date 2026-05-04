"""Pydantic models for orbits.yaml and chart.yaml.

Per spec §2.1–§2.2: orbits.yaml is the plotter's only input (mechanics);
chart.yaml is renderer-only (flavor); they live in the per-world content
directory and are loaded by `sidequest.orbital.loader`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TravelRealism(StrEnum):
    NARRATIVE = "narrative"
    HYBRID = "hybrid"
    ORBITAL = "orbital"


class BodyType(StrEnum):
    STAR = "star"
    COMPANION = "companion"
    HABITAT = "habitat"
    ARC_BELT = "arc_belt"
    GATE = "gate"
    WRECK = "wreck"


class ClockConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch_days: float = 0.0


class TravelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    realism: TravelRealism = TravelRealism.NARRATIVE
    travel_speed_factor: float = Field(default=1.0, gt=0.0)
    danger_density: float = Field(default=0.0, ge=0.0)
    hazard_arc_density: float = Field(default=0.0, ge=0.0)


Register = Literal["engraved", "chalk", "prose"]
"""Cartographic register — drives both orbit and label styling per spec §4.4.

- `engraved`: stamped, official, certain. Solid brass orbit + Orbitron CAPS label.
- `chalk`: hand-charted, frontier. Dashed orbit + Orbitron weight-600 CAPS label.
- `prose`: in-world flavor. (Default-derived) inherits parent's orbit register;
  label is VT323 italic monospace. Used for moons and "prose" name overrides.
"""


class BodyDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: BodyType
    parent: str | None = None
    semi_major_au: float | None = None
    period_days: float | None = None
    epoch_phase_deg: float | None = None
    eccentricity: float = 0.0
    arc_extent_deg: float | None = None
    hazard: bool = False
    hazard_table: str | None = None
    label: str | None = None
    label_color: str | None = None
    subtype: str | None = None  # e.g. "gas_giant", "rocky" — drives glyph variant

    # ---- Spec §4.4 — register and label_register ----
    # `register` drives both orbit stroke styling and (default) label styling.
    # `label_register` is an opt-in override for the rare case where the
    # cartographic register of the *orbit* differs from the *label* — e.g.
    # `last_drift` has a chalk-drawn orbit but its label is in prose register
    # (lowercase italic). When None, label inherits from `register`.
    register: Register = "engraved"
    label_register: Register | None = None

    # ---- Spec §4.6 — moon-band display at system-root scope ----
    # `moon_display_radius_px` pins the moon's pixel radius around its parent
    # at system-root scope. None = auto-allocate (closest first at MOON_BAND_INNER_PX,
    # step outward by MOON_BAND_STEP_PX).
    moon_display_radius_px: int | None = None
    # `show_at_system_scope=False` elides a moon from system-root rendering
    # entirely (still visible at drill-in scope). For trivia bodies the
    # designer doesn't want cluttering the chart.
    show_at_system_scope: bool = True

    @model_validator(mode="after")
    def _validate_orbital_params(self) -> BodyDef:
        if self.parent is not None:
            for fld in ("semi_major_au", "period_days", "epoch_phase_deg"):
                if getattr(self, fld) is None:
                    raise ValueError(f"body with parent={self.parent!r} requires {fld}; got None")
        if self.type == BodyType.ARC_BELT and self.arc_extent_deg is None:
            raise ValueError("body with type=arc_belt requires arc_extent_deg; got None")
        return self

    @model_validator(mode="after")
    def _validate_label_not_blank(self) -> BodyDef:
        if self.label is not None and not self.label.strip():
            raise ValueError("label must be non-empty if provided")
        return self


class ConjunctionPair(BaseModel):
    """A pair of bodies whose alignment events the chart watches.

    The renderer surfaces the soonest event across all pairs as the
    "NEXT CONJUNCTION" countdown in the chart HUD. Per spec §11 AC2.
    """

    model_config = ConfigDict(extra="forbid")
    body_a: str
    body_b: str
    label: str | None = None  # display name; defaults to "{a.label} ↔ {b.label}"


class OrbitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    clock: ClockConfig
    travel: TravelConfig
    bodies: dict[str, BodyDef]
    conjunctions: list[ConjunctionPair] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_parent_refs(self) -> OrbitsConfig:
        ids = set(self.bodies.keys())
        for body_id, body in self.bodies.items():
            if body.parent is not None and body.parent not in ids:
                raise ValueError(
                    f"body {body_id!r} has unknown parent {body.parent!r}; "
                    f"available bodies: {sorted(ids)}"
                )
        return self

    @model_validator(mode="after")
    def _validate_conjunctions(self) -> OrbitsConfig:
        ids = set(self.bodies.keys())
        for pair in self.conjunctions:
            if pair.body_a == pair.body_b:
                raise ValueError(
                    f"conjunction pair must reference two different bodies; "
                    f"both are {pair.body_a!r}"
                )
            for side, body_id in (("body_a", pair.body_a), ("body_b", pair.body_b)):
                if body_id not in ids:
                    raise ValueError(
                        f"conjunction {pair.body_a}↔{pair.body_b} {side}={body_id!r} "
                        f"is not in bodies; available: {sorted(ids)}"
                    )
            # Both bodies must share a common ancestor for angular separation
            # to be meaningful (otherwise we'd be measuring across coordinate
            # systems with no shared origin).
            ancestors_a = _ancestor_chain(self.bodies, pair.body_a)
            ancestors_b = _ancestor_chain(self.bodies, pair.body_b)
            common = ancestors_a & ancestors_b
            if not common:
                raise ValueError(
                    f"conjunction pair {pair.body_a}↔{pair.body_b} bodies have no "
                    f"common ancestor; angular separation would be ill-defined"
                )
        return self


def _ancestor_chain(bodies: dict[str, BodyDef], body_id: str) -> set[str]:
    """Return body_id and all its ancestors (parent, grandparent, …)."""
    chain: set[str] = {body_id}
    current = bodies[body_id].parent
    seen: set[str] = set()
    while current is not None and current not in seen:
        chain.add(current)
        seen.add(current)
        if current not in bodies:
            break
        current = bodies[current].parent
    return chain


KNOWN_ANNOTATION_KINDS: frozenset[str] = frozenset(
    {
        "engraved_label",
        "glyph",
        "scale_ruler",
        "bearing_marks",
        "anomaly_marker",
        "lagrange_point",
        "flight_corridor",
        "callout_label",
    }
)
"""Annotation kinds the renderer knows how to draw. Per CLAUDE.md "no silent
fallbacks", an unknown kind raises at chart-load rather than disappearing
silently at render time. Add new kinds here AND in `render._render_annotation`
together — keeping the registry alongside the validator catches the half-wired
case at load."""


class Annotation(BaseModel):
    """Chart-only flavor element. `kind` selects renderer behavior;
    other fields are per-kind (validated leniently — renderer asserts
    what it needs)."""

    model_config = ConfigDict(extra="forbid")
    kind: str
    text: str | None = None
    caption: str | None = None
    curve_along: str | None = None
    at: dict[str, Any] | None = None
    style: str | None = None
    body_ref: str | None = None
    bearings: list[float] | None = None
    label: str | None = None
    tag: str | None = None  # ADR-094 — only meaningful when kind == "callout_label"

    @model_validator(mode="after")
    def _validate_known_kind(self) -> Annotation:
        if self.kind not in KNOWN_ANNOTATION_KINDS:
            raise ValueError(
                f"unknown annotation kind {self.kind!r}; "
                f"known kinds: {sorted(KNOWN_ANNOTATION_KINDS)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_callout_label(self) -> Annotation:
        if self.kind != "callout_label":
            return self
        if self.text is None or not self.text.strip():
            raise ValueError("callout_label requires non-empty text")
        if not self.body_ref:
            raise ValueError("callout_label requires body_ref")
        if self.tag is not None:
            from sidequest.orbital.palette import CALLOUT_TAG_MAX_CHARS

            if len(self.tag) > CALLOUT_TAG_MAX_CHARS:
                raise ValueError(
                    f"callout_label tag exceeds {CALLOUT_TAG_MAX_CHARS} chars: "
                    f"{self.tag!r} ({len(self.tag)} chars)"
                )
        return self


class ChartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    annotations: list[Annotation] = Field(default_factory=list)
