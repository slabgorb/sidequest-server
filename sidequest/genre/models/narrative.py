"""Narrative support types: prompts, openings, beat vocabulary, achievements, power tiers.

Port of sidequest-genre/src/models/narrative.rs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Prompts(BaseModel):
    """LLM prompt templates for different agent roles."""

    model_config = {"extra": "forbid"}

    narrator: str
    combat: str
    npc: str
    world_state: str
    chase: str | None = None
    transition_hints: dict[str, str] = Field(default_factory=dict)
    extraction: str | None = None
    keeper_monologue: str | None = None
    town: str | None = None
    chargen: str | None = None


class OpeningHook(BaseModel):
    """An opening scenario hook."""

    model_config = {"extra": "forbid"}

    id: str
    archetype: str
    situation: str
    tone: str
    avoid: list[str] = Field(default_factory=list)
    first_turn_seed: str


class BeatObstacle(BaseModel):
    """A chase obstacle."""

    model_config = {"extra": "forbid"}

    name: str
    description: str
    stat_check: str
    failure_penalty: str
    tags: list[str] = Field(default_factory=list)


class BeatVocabulary(BaseModel):
    """Chase/beat vocabulary configuration."""

    model_config = {"extra": "forbid"}

    obstacles: list[BeatObstacle] = Field(default_factory=list)


class Achievement(BaseModel):
    """An achievement linked to trope progression."""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    description: str
    trope_id: str
    trigger_status: str
    emoji: str


class PowerTier(BaseModel):
    """A power tier description for a character class at a level range."""

    model_config = {"extra": "forbid"}

    level_range: list[int]
    label: str
    player: str
    npc: str | None = None
