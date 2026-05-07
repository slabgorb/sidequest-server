"""Pydantic models for the magic system.

All models use ``extra='forbid'`` per project no-silent-fallback rule.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# --- World-knowledge axis ----------------------------------------------------

# Awareness ordering: lower index = less institutionally aware that magic is real.
# "folkloric" means educated folk dismiss it as superstition — less real-awareness
# than "classified" (power structures know and hide it) or "esoteric" (initiates know).
# Order: denied < folkloric < mythic_lapsed < esoteric < classified < acknowledged
_AWARENESS_ORDER = (
    "denied",
    "folkloric",
    "mythic_lapsed",
    "esoteric",
    "classified",
    "acknowledged",
)


class WorldKnowledge(BaseModel):
    """How aware the world is that magic is a real category.

    ``local_register`` is an optional sub-tag for worlds where the legal/
    political register and the folk register diverge (Coyote Star: Hegemony
    classifies; frontier folks know it folklorically).
    """

    model_config = {"extra": "forbid"}

    # Order here matches `_AWARENESS_ORDER` (least-aware → most-aware) so the
    # field declaration documents the same axis the validator enforces.
    primary: Literal[
        "denied", "folkloric", "mythic_lapsed", "esoteric", "classified", "acknowledged"
    ]
    local_register: (
        Literal[
            "denied",
            "folkloric",
            "mythic_lapsed",
            "esoteric",
            "classified",
            "acknowledged",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def local_register_le_primary(self) -> WorldKnowledge:
        if self.local_register is None:
            return self
        primary_idx = _AWARENESS_ORDER.index(self.primary)
        local_idx = _AWARENESS_ORDER.index(self.local_register)
        if local_idx > primary_idx:
            raise ValueError(
                f"local_register={self.local_register!r} exceeds primary={self.primary!r} "
                f"in awareness ordering"
            )
        return self


# --- Magic working event -----------------------------------------------------


class MagicWorking(BaseModel):
    """A single magic event emitted by the narrator in game_patch.magic_working."""

    model_config = {"extra": "forbid"}

    plugin: str
    mechanism: Literal[
        "faction", "place", "time", "condition", "native", "discovery", "relational", "cosmic"
    ]
    actor: str
    costs: dict[str, float] = Field(default_factory=dict)
    domain: Literal[
        "elemental",
        "physical",
        "psychic",
        "spatial",
        "temporal",
        "necromantic",
        "illusory",
        "divinatory",
        "transmutative",
        "alchemical",
    ]
    narrator_basis: str
    # Plugin-specific fields. Validators enforce per-plugin requirements.
    flavor: str | None = None
    consent_state: str | None = None
    item_id: str | None = None
    alignment_with_item_nature: float | None = None

    @model_validator(mode="after")
    def costs_non_negative(self) -> MagicWorking:
        for k, v in self.costs.items():
            if v < 0:
                raise ValueError(f"cost {k}={v} must be >= 0")
        return self


# --- Ledger bar spec ---------------------------------------------------------


class StatusPromotion(BaseModel):
    """Per-bar config: how a threshold crossing surfaces in the Status panel.

    World-content, not engine code (architect §5.3, 2026-04-29) — different
    worlds may map the same bar id to different status text/severity. A bar
    that omits this block produces no auto-promoted Status; the silent skip
    is intentional, not a fallback.
    """

    model_config = {"extra": "forbid"}

    text: str
    severity: Literal["Scratch", "Wound", "Scar", "Boon"]


class LedgerBarSpec(BaseModel):
    """Per-bar configuration loaded from world magic.yaml."""

    model_config = {"extra": "forbid"}

    id: str
    scope: Literal["character", "world", "item", "faction", "location", "bond_pair"]
    direction: Literal["up", "down", "bidirectional"]
    range: tuple[float, float]
    threshold_high: float | None = None
    threshold_higher: float | None = None
    threshold_low: float | None = None
    threshold_lower: float | None = None
    consequence_on_high_cross: str | None = None
    consequence_on_low_cross: str | None = None
    decay_per_session: float = 0.0
    # Initial value at character-add or world-load time. Two shapes:
    #   - scalar float — every owner starts at the same value
    #   - dict[class, float] — character-scope only; the value is keyed
    #     by ``character.char_class`` (display-cased: "Mage", "Cleric",
    #     etc.). Used by B/X-style class-aware spell slot allocation
    #     (caverns_and_claudes/caverns_sunden 2026-05-07 pivot). When
    #     a dict-shaped spec is encountered, ``MagicState.add_character``
    #     requires the caller to pass ``character_class``; missing or
    #     unknown class raises ValueError (no silent fallback per CLAUDE.md).
    starts_at_chargen: float | dict[str, float]
    # Per-bar status-panel mapping (Task 3.4). Optional: bars that don't
    # surface as character statuses (world-scope hegemony_heat, etc.) leave
    # this None. The threshold-promotion pipeline reads this directly off
    # ``snapshot.magic_state.config.ledger_bars[bar_id].promote_to_status``
    # so the mapping stays world-tunable without engine code changes.
    promote_to_status: StatusPromotion | None = None

    @model_validator(mode="after")
    def thresholds_match_direction(self) -> LedgerBarSpec:
        if self.direction == "down" and self.threshold_low is None:
            raise ValueError(f"bar {self.id!r} direction=down requires threshold_low")
        if self.direction == "up" and self.threshold_high is None:
            raise ValueError(f"bar {self.id!r} direction=up requires threshold_high")
        if self.direction == "bidirectional" and (
            self.threshold_low is None or self.threshold_high is None
        ):
            raise ValueError(
                f"bar {self.id!r} direction=bidirectional requires both threshold_low and threshold_high"
            )
        return self

    @model_validator(mode="after")
    def class_keyed_starts_only_for_character_scope(self) -> LedgerBarSpec:
        if isinstance(self.starts_at_chargen, dict) and self.scope != "character":
            raise ValueError(
                f"bar {self.id!r} uses class-keyed starts_at_chargen but scope="
                f"{self.scope!r}; only character-scope bars may key by class"
            )
        if isinstance(self.starts_at_chargen, dict) and not self.starts_at_chargen:
            raise ValueError(
                f"bar {self.id!r} starts_at_chargen dict is empty; list at least one class"
            )
        return self

    @model_validator(mode="after")
    def range_and_thresholds_in_bounds(self) -> LedgerBarSpec:
        lo, hi = self.range
        if not lo < hi:
            raise ValueError(f"bar {self.id!r} range={self.range!r} must satisfy lo < hi")
        for name in (
            "threshold_high",
            "threshold_higher",
            "threshold_low",
            "threshold_lower",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if not lo <= value <= hi:
                raise ValueError(
                    f"bar {self.id!r} {name}={value!r} must lie within range={self.range!r}"
                )
        # ``starts_at_chargen`` is float | dict[str, float]; check both shapes.
        starts = self.starts_at_chargen
        starts_values = starts.values() if isinstance(starts, dict) else (starts,)
        for v in starts_values:
            if not lo <= v <= hi:
                raise ValueError(
                    f"bar {self.id!r} starts_at_chargen={starts!r} contains value {v} "
                    f"outside range={self.range!r}"
                )
        return self


# --- Hard limit --------------------------------------------------------------


class HardLimit(BaseModel):
    """A named impossibility for the genre/world."""

    model_config = {"extra": "forbid"}

    id: str
    description: str
    references_plugin: str | None = None  # for plugin-lane-respect citations


# --- Plugin descriptor (loaded from plugin yaml) -----------------------------


class Plugin(BaseModel):
    """Static plugin descriptor — content from plugin .yaml file.

    Mechanics live in the paired .py file and reach this descriptor through
    the MAGIC_PLUGINS module-level dict (see plugin.py).
    """

    model_config = {"extra": "forbid"}

    plugin_id: str
    source: Literal["innate", "learned", "item_based", "divine", "bargained_for"]
    delivery_mechanisms: list[str]
    ledger_bar_templates: dict[str, LedgerBarSpec]
    narrator_register: str
    required_span_attrs: list[str]
    optional_span_attrs: list[str] = Field(default_factory=list)


# --- Validator output --------------------------------------------------------


class FlagSeverity(StrEnum):
    YELLOW = "yellow"
    RED = "red"
    DEEP_RED = "deep_red"


class Flag(BaseModel):
    model_config = {"extra": "forbid"}

    severity: FlagSeverity
    reason: str
    detail: str = ""


# --- World magic config (composition root for a world) -----------------------


class WorldMagicConfig(BaseModel):
    """Materialized magic configuration for one world."""

    model_config = {"extra": "forbid"}

    world_slug: str
    genre_slug: str
    allowed_sources: list[str]
    active_plugins: list[str]
    intensity: float = Field(ge=0.0, le=1.0)
    world_knowledge: WorldKnowledge
    visibility: dict[str, str]  # e.g. {"primary": "feared", "local_register": "dismissed"}
    hard_limits: list[HardLimit]
    cost_types: list[str]
    ledger_bars: list[LedgerBarSpec]  # bars instantiated at world-load
    can_build_caster: bool = False
    can_build_item_user: bool = True
    narrator_register: str
