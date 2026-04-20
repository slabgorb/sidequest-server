"""Trope definition types from tropes.yaml.

Port of sidequest-genre/src/models/tropes.rs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TropeEscalation(BaseModel):
    """A single escalation step within a trope."""

    model_config = {"extra": "forbid"}

    at: float
    event: str
    npcs_involved: list[str] = Field(default_factory=list)
    stakes: str = ""


class PassiveProgression(BaseModel):
    """Passive progression configuration for a trope."""

    model_config = {"extra": "forbid"}

    rate_per_turn: float = 0.0
    rate_per_day: float = 0.0
    accelerators: list[str] = Field(default_factory=list)
    decelerators: list[str] = Field(default_factory=list)
    accelerator_bonus: float = 0.0
    decelerator_penalty: float = 0.0


class TropeDefinition(BaseModel):
    """A narrative trope definition (genre-level or world-level)."""

    model_config = {"extra": "forbid"}

    id: str | None = None
    name: str
    description: str | None = None
    category: str = ""
    triggers: list[str] = Field(default_factory=list)
    narrative_hints: list[str] = Field(default_factory=list)
    tension_level: float | None = None
    resolution_hints: list[str] | None = None
    resolution_patterns: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    escalation: list[TropeEscalation] = Field(default_factory=list)
    passive_progression: PassiveProgression | None = None
    is_abstract: bool = Field(default=False, alias="abstract")
    extends: str | None = None

    model_config = {"extra": "forbid", "populate_by_name": True}
