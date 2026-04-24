"""Game rules, resource declarations, and confrontation types from rules.yaml.

Port of sidequest-genre/src/models/rules.rs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class InitiativeRule(BaseModel):
    """Maps an encounter type to its primary stat for turn ordering."""

    model_config = {"extra": "forbid"}

    primary_stat: str
    description: str


class ResourceThresholdDecl(BaseModel):
    """A threshold on a resource declaration."""

    model_config = {"extra": "forbid"}

    at: float
    event_id: str
    narrator_hint: str


class ResourceDeclaration(BaseModel):
    """Genre resource declaration (e.g., Luck, Humanity, Heat)."""

    model_config = {"extra": "forbid"}

    name: str
    label: str
    min: float
    max: float
    starting: float
    voluntary: bool
    decay_per_turn: float
    thresholds: list[ResourceThresholdDecl] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_range(self) -> ResourceDeclaration:
        if self.max < self.min:
            raise ValueError(
                f"resource '{self.name}': max ({self.max}) must be >= min ({self.min})"
            )
        if not (self.min <= self.starting <= self.max):
            raise ValueError(
                f"resource '{self.name}': starting ({self.starting}) must be in "
                f"[{self.min}, {self.max}]"
            )
        return self


class SecondaryStatDef(BaseModel):
    """A secondary stat derived from an ability score."""

    model_config = {"extra": "forbid"}

    name: str
    source_stat: str
    spendable: bool


class BeatDef(BaseModel):
    """A single action available during a confrontation."""

    model_config = {"extra": "forbid"}

    id: str
    label: str
    metric_delta: int
    stat_check: str
    # Legacy freeform documentation of the failure branch. Prefer the
    # structured ``failure_metric_delta`` + ``failure_effect`` below when
    # the beat has mechanical failure consequences — ``risk`` is for the
    # narrator's prose cue only and does NOT drive the engine.
    risk: str | None = None
    # Failure branch (ADR-074 dice resolution integration). When a dice
    # roll classifies the beat as Fail / CritFail, the encounter engine
    # substitutes ``failure_metric_delta`` for ``metric_delta`` and passes
    # ``failure_effect`` to the narrator as a structured cue. Both are
    # optional — beats without a failure branch keep the legacy behavior
    # (always apply ``metric_delta``).
    failure_metric_delta: int | None = None
    failure_effect: str | None = None
    reveals: str | None = None
    resolution: bool | None = None
    effect: str | None = None
    consequence: str | None = None
    requires: str | None = None
    narrator_hint: str | None = None
    gold_delta: int | None = None
    edge_delta: int | None = None
    target_edge_delta: int | None = None
    resource_deltas: dict[str, float] | None = None

    @model_validator(mode="after")
    def _validate_id(self) -> BeatDef:
        if not self.id:
            raise ValueError("beat id must not be empty")
        return self


class MetricDef(BaseModel):
    """The primary tracking metric for a confrontation type."""

    model_config = {"extra": "forbid"}

    name: str
    direction: str
    starting: int
    threshold_high: int | None = None
    threshold_low: int | None = None

    @model_validator(mode="after")
    def _validate_direction(self) -> MetricDef:
        valid = {"ascending", "descending", "bidirectional"}
        if self.direction not in valid:
            raise ValueError(
                f"invalid metric direction '{self.direction}': must be one of {valid}"
            )
        return self


class ResolutionMode(str, Enum):
    """How a confrontation resolves each turn."""

    beat_selection = "beat_selection"
    sealed_letter_lookup = "sealed_letter_lookup"


class InteractionCell(BaseModel):
    """A single cell of a sealed-letter interaction table."""

    model_config = {"extra": "forbid"}

    pair: list[str]  # exactly 2 items: [red, blue]
    name: str = ""
    shape: str = ""
    red_view: Any = None
    blue_view: Any = None
    narration_hint: str = ""
    tags: list[str] = Field(default_factory=list)
    calibration_notes: str | None = None

    @model_validator(mode="after")
    def _validate_pair(self) -> InteractionCell:
        if len(self.pair) != 2:
            raise ValueError(
                f"interaction cell pair must have exactly 2 elements, got {len(self.pair)}"
            )
        return self


class InteractionTable(BaseModel):
    """A sealed-letter interaction table."""

    model_config = {"extra": "forbid"}

    version: str
    starting_state: str
    maneuvers_consumed: list[str] = Field(default_factory=list)
    cells: list[InteractionCell] = Field(default_factory=list)
    damage_increments: dict[str, int] | None = None
    starting_hull: int | None = None

    @model_validator(mode="after")
    def _validate(self) -> InteractionTable:
        if not self.version:
            raise ValueError("interaction table version must not be empty")
        if not self.cells:
            raise ValueError("interaction table must have at least one cell")
        seen: set[tuple[str, str]] = set()
        for cell in self.cells:
            key = (cell.pair[0], cell.pair[1])
            if key in seen:
                raise ValueError(
                    f"duplicate interaction cell pair: ({cell.pair[0]}, {cell.pair[1]})"
                )
            seen.add(key)
        if self.damage_increments is not None:
            for tier in ("graze", "clean", "devastating"):
                val = self.damage_increments.get(tier)
                if val is None:
                    raise ValueError(
                        f"damage_increments missing required severity tier: '{tier}'"
                    )
                if val <= 0:
                    raise ValueError(
                        f"damage_increments '{tier}' must be positive, got {val}"
                    )
        return self


class ConfrontationDef(BaseModel):
    """A confrontation type declared by a genre pack in rules.yaml."""

    model_config = {"extra": "forbid"}

    confrontation_type: str = Field(alias="type", serialization_alias="type")
    label: str
    category: str
    resolution_mode: ResolutionMode = ResolutionMode.beat_selection
    metric: MetricDef
    beats: list[BeatDef] = Field(default_factory=list)
    secondary_stats: list[SecondaryStatDef] = Field(default_factory=list)
    escalates_to: str | None = None
    mood: str | None = None
    interaction_table: InteractionTable | None = None

    model_config = {"extra": "forbid", "populate_by_name": True}

    @model_validator(mode="after")
    def _validate(self) -> ConfrontationDef:
        if not self.confrontation_type:
            raise ValueError("confrontation type must not be empty")
        valid_categories = {"combat", "social", "pre_combat", "movement"}
        if self.category not in valid_categories:
            raise ValueError(
                f"invalid confrontation category '{self.category}': "
                f"must be one of {valid_categories}"
            )
        if not self.beats:
            raise ValueError(
                f"confrontation '{self.confrontation_type}' must have at least one beat"
            )
        seen: set[str] = set()
        for beat in self.beats:
            if beat.id in seen:
                raise ValueError(
                    f"confrontation '{self.confrontation_type}' has duplicate beat id '{beat.id}'"
                )
            seen.add(beat.id)
        return self


class CrossingDirection(str, Enum):
    """Direction in which an EdgeThresholdDecl fires."""

    crossing_down = "crossing_down"


class RecoveryBehaviour(str, Enum):
    """Recovery behaviour for an edge pool at a named cadence."""

    full = "full"


class EdgeThresholdDecl(BaseModel):
    """Downward threshold declared in edge_config.thresholds."""

    model_config = {"extra": "forbid"}

    at: int
    event_id: str
    narrator_hint: str
    direction: CrossingDirection | None = None


class EdgeRecoveryDefaults(BaseModel):
    """Default recovery behaviour for composure pools."""

    model_config = {"extra": "forbid"}

    on_resolution: RecoveryBehaviour | None = None
    on_long_rest: RecoveryBehaviour | None = None
    between_back_to_back: int | None = None


class EdgeConfig(BaseModel):
    """Per-genre Edge / Composure configuration."""

    model_config = {"extra": "forbid"}

    base_max_by_class: dict[str, int] = Field(default_factory=dict)
    recovery_defaults: EdgeRecoveryDefaults = Field(default_factory=EdgeRecoveryDefaults)
    thresholds: list[EdgeThresholdDecl] = Field(default_factory=list)
    display_fields: list[str] = Field(default_factory=list)


class RulesConfig(BaseModel):
    """Game rules configuration."""

    model_config = {"extra": "forbid"}

    tone: str = ""
    lethality: str = ""
    magic_level: str = ""
    stat_generation: str = ""
    point_buy_budget: int = 0
    ability_score_names: list[str] = Field(default_factory=list)
    allowed_classes: list[str] = Field(default_factory=list)
    allowed_races: list[str] = Field(default_factory=list)
    class_hp_bases: dict[str, int] = Field(default_factory=dict)
    edge_config: EdgeConfig | None = None
    default_class: str | None = None
    default_race: str | None = None
    default_hp: int | None = None
    default_ac: int | None = None
    race_label: str | None = None
    class_label: str | None = None
    default_location: str | None = None
    default_time_of_day: str | None = None
    hp_formula: str | None = None
    banned_spells: list[str] = Field(default_factory=list)
    custom_rules: dict[str, str] = Field(default_factory=dict)
    stat_display_fields: list[str] = Field(default_factory=list)
    encounter_base_tension: dict[str, float] = Field(default_factory=dict)
    resources: list[ResourceDeclaration] = Field(default_factory=list)
    confrontations: list[ConfrontationDef] = Field(default_factory=list)
    xp_affinity: str | None = None
    initiative_rules: dict[str, InitiativeRule] = Field(default_factory=dict)
    # spaghetti_western authored mechanics — Rust dropped them; accepted as
    # pass-through until a consumer wires the standoff / reputation systems.
    standoff_rules: dict[str, Any] = Field(default_factory=dict)
    reputation_factions: list[dict[str, Any]] = Field(default_factory=list)
    reputation_effects: dict[str, Any] = Field(default_factory=dict)
    luck_rules: dict[str, Any] = Field(default_factory=dict)
