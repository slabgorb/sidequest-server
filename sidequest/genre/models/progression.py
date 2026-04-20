"""Character progression types from progression.yaml.

Port of sidequest-genre/src/models/progression.rs.
"""

from __future__ import annotations

from typing import Any, Union

from pydantic import BaseModel, Field, model_validator

from sidequest.genre.models.advancement import AdvancementEffect


class Ability(BaseModel):
    """An ability within an affinity tier.

    Can be either a simple string description or a full struct.
    """

    model_config = {"extra": "forbid"}

    name: str
    experience: str = ""
    limits: str = ""

    @classmethod
    def model_validate(cls, obj: object, **kwargs: Any) -> "Ability":  # type: ignore[override]
        """Accept a plain string as name-only form."""
        if isinstance(obj, str):
            return cls(name=obj)
        return super().model_validate(obj, **kwargs)


class AffinityTier(BaseModel):
    """A single tier within an affinity."""

    model_config = {"extra": "forbid"}

    name: str
    description: str
    abilities: list[Ability] = Field(default_factory=list)
    mechanical_effects: list[AdvancementEffect] | None = None


class AffinityUnlocks(BaseModel):
    """Tier unlocks for an affinity (fixed set: tier_0–tier_3)."""

    model_config = {"extra": "forbid"}

    tier_0: AffinityTier | None = None
    tier_1: AffinityTier | None = None
    tier_2: AffinityTier | None = None
    tier_3: AffinityTier | None = None


class Affinity(BaseModel):
    """A skill/affinity tree."""

    model_config = {"extra": "forbid"}

    name: str
    description: str
    triggers: list[str] = Field(default_factory=list)
    tier_thresholds: list[int] = Field(default_factory=list)
    unlocks: AffinityUnlocks | None = None


class ItemEvolution(BaseModel):
    """Item evolution thresholds."""

    model_config = {"extra": "forbid"}

    naming_threshold: float = 0.0
    power_up_threshold: float = 0.0


class LevelBonuses(BaseModel):
    """Per-level bonuses."""

    model_config = {"extra": "forbid"}

    stat_points: int = 0
    hp_bonus: str = ""


class WealthTier(BaseModel):
    """A wealth tier with optional gold cap."""

    model_config = {"extra": "forbid"}

    max_gold: int | None = None
    label: str


class ProgressionConfig(BaseModel):
    """Character progression configuration."""

    model_config = {"extra": "forbid"}

    affinities: list[Affinity] = Field(default_factory=list)
    milestone_categories: list[str] = Field(default_factory=list)
    milestones_per_level: int = 0
    max_level: int = 0
    item_evolution: ItemEvolution | None = None
    level_bonuses: LevelBonuses | None = None
    wealth_tiers: list[WealthTier] = Field(default_factory=list)
