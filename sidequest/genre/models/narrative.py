"""Narrative support types: prompts, openings, beat vocabulary, achievements, power tiers.

Port of sidequest-genre/src/models/narrative.rs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Prompts(BaseModel):
    """LLM prompt templates for different agent roles.

    Genre-specific prompts (``ritual``, ``debt_collection``,
    ``session_opener_template``) are authored in heavy_metal and
    spaghetti_western. Rust silently dropped them; accepted here as
    pass-through. Consumers should look them up by key when a genre
    context triggers the corresponding scene.
    """

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
    ritual: str | None = None
    debt_collection: str | None = None
    session_opener_template: str | None = None


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
    """Chase/beat vocabulary configuration.

    heavy_metal authored ``event_flavor``, ``decision_framings``, and
    ``chase_modes`` as additional prose/pacing hooks. Rust silently dropped
    them; accepted here as pass-through.
    """

    model_config = {"extra": "forbid"}

    obstacles: list[BeatObstacle] = Field(default_factory=list)
    event_flavor: list[dict[str, Any]] = Field(default_factory=list)
    decision_framings: list[str] = Field(default_factory=list)
    chase_modes: list[str] = Field(default_factory=list)


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
