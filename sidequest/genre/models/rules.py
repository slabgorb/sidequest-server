"""Game rules, resource declarations, and confrontation types from rules.yaml.

Port of sidequest-genre/src/models/rules.rs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from sidequest.game.beat_kinds import BeatKind


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
    """A single action available during a confrontation.

    Schema (spec 2026-04-25-dual-track-momentum-design.md §Schema changes):

    - ``kind``: closed enum driving per-tier delta defaults.
    - ``base``: scalar magnitude; meaning depends on kind.
    - ``deltas``: optional per-tier override map; keys ∈
      {crit_fail, fail, tie, success, crit_success}; values are dicts of
      {own, opponent, grants_tag, grants_fleeting_tag, resolution, ...}.
    - ``target_tag``: required for kind=angle; text of the tag created.
    - Legacy ``metric_delta``/``failure_metric_delta``/``failure_effect``
      are deleted — pack migration is mandatory.
    """

    model_config = {"extra": "forbid"}

    id: str
    label: str
    kind: BeatKind
    base: int = 1
    deltas: dict[str, dict[str, Any]] | None = None
    target_tag: str | None = None
    stat_check: str
    risk: str | None = None  # narrator prose cue only — does not drive engine
    reveals: str | None = None
    resolution: bool | None = None  # legacy "always-resolves" flag (still useful for declarative pushes)
    effect: str | None = None
    consequence: str | None = None
    requires: str | None = None
    narrator_hint: str | None = None
    gold_delta: int | None = None
    edge_delta: int | None = None
    target_edge_delta: int | None = None
    resource_deltas: dict[str, float] | None = None

    @model_validator(mode="after")
    def _validate(self) -> BeatDef:
        if not self.id:
            raise ValueError("beat id must not be empty")
        if self.kind is BeatKind.angle and not self.target_tag:
            raise ValueError(
                f"beat '{self.id}' kind=angle requires target_tag"
            )
        if self.deltas is not None:
            valid_tiers = {
                "crit_fail", "fail", "tie", "success", "crit_success",
            }
            for tier in self.deltas:
                if tier not in valid_tiers:
                    raise ValueError(
                        f"beat '{self.id}' deltas key {tier!r} not in {valid_tiers}"
                    )
        return self


class MetricDef(BaseModel):
    """Per-side ascending metric for a confrontation.

    Spec change: bidirectional/descending metrics are gone — both sides have
    independent ascending dials. ``threshold`` is the cross point.
    """

    model_config = {"extra": "forbid"}

    name: str
    starting: int = 0
    threshold: int

    @model_validator(mode="after")
    def _validate(self) -> MetricDef:
        if self.threshold <= self.starting:
            raise ValueError(
                f"metric '{self.name}' threshold ({self.threshold}) must be "
                f"> starting ({self.starting})"
            )
        return self


class ResolutionMode(str, Enum):  # noqa: UP042 — matches project convention (see protocol/enums.py)
    """How a confrontation resolves each turn.

    - ``beat_selection``: player rolls d20 vs static DC. Tier drives delta
      application. Opponent outcome tier is narrator-fiat (no opposing roll).
    - ``sealed_letter_lookup``: simultaneous-commit cell-table resolution
      (dogfight, ADR-077).
    - ``opposed_check``: BOTH sides roll d20 + modifier; outcome tier is
      derived from the shift between rolls (Fate-style bands). Combat
      encounters use this so the opponent dial only advances when the
      opponent's roll actually beats the player's. The narrator picks
      WHICH beat the opponent took, but never the outcome tier — the
      engine derives it from the dice. See:
      ``.archive/handoffs/opposed-checks-design.md``.
    """

    beat_selection = "beat_selection"
    sealed_letter_lookup = "sealed_letter_lookup"
    opposed_check = "opposed_check"


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

    model_config = {"extra": "forbid", "populate_by_name": True}

    confrontation_type: str = Field(alias="type", serialization_alias="type")
    label: str
    category: str
    resolution_mode: ResolutionMode = ResolutionMode.beat_selection
    player_metric: MetricDef
    opponent_metric: MetricDef
    beats: list[BeatDef] = Field(default_factory=list)
    secondary_stats: list[SecondaryStatDef] = Field(default_factory=list)
    escalates_to: str | None = None
    mood: str | None = None
    interaction_table: InteractionTable | None = None
    # Genre-level opponent stat fallback. Used by opposed_check resolution
    # when an EncounterActor lacks a per_actor_state.stats entry for the
    # beat's stat_check. Maps stat name → raw ability score (the same
    # 3..20 D&D-style score the player side uses; modifier is derived
    # via floor((score-10)/2)). Hard-fail-loud when neither this map nor
    # the per-actor block carries the stat (CLAUDE.md no-silent-fallback).
    # ``None`` means the pack has not migrated this confrontation to
    # opposed_check — only valid when ``resolution_mode`` is something
    # other than ``opposed_check``.
    opponent_default_stats: dict[str, int] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_metric(cls, data: object) -> object:
        if isinstance(data, dict) and "metric" in data:
            raise ValueError(
                "confrontation uses legacy single 'metric' shape; "
                "migrate to player_metric + opponent_metric per "
                "docs/superpowers/specs/2026-04-25-dual-track-momentum-design.md"
            )
        return data

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
    # Per-pack character-sheet vocabulary. Keys are the canonical chargen
    # field names (``name``, ``race``, ``class``, ``personality``,
    # ``pronouns``, ``stats``, ``mutation``, ``affinity``, ``rig``,
    # ``rig_trait``, ``equipment``, ``backstory``); values are the
    # display labels used by the confirmation summary and the
    # client-side character-sheet preview. Empty by default — packs
    # opt in. Defaults preserve the legacy fantasy labels (``Race``,
    # ``Class``, etc.) so existing packs are unaffected. The legacy
    # ``race_label`` / ``class_label`` fields above are honored as
    # secondary defaults for those two keys when this map omits them.
    chargen_field_labels: dict[str, str] = Field(default_factory=dict)
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
