"""Base archetype axis definitions from archetype_axes.rs.

Port of sidequest-genre/src/models/archetype_axes.rs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OceanTendencies(BaseModel):
    """OCEAN score ranges — [min, max] for each Big Five dimension."""

    model_config = {"extra": "forbid"}

    openness: list[float]
    conscientiousness: list[float]
    extraversion: list[float]
    agreeableness: list[float]
    neuroticism: list[float]


class JungianArchetype(BaseModel):
    """Base Jungian archetype — personality core, genre-agnostic."""

    model_config = {"extra": "forbid"}

    id: str
    drive: str
    ocean_tendencies: OceanTendencies
    stat_affinity: list[str] = Field(default_factory=list)


class RpgRole(BaseModel):
    """Base RPG role — mechanical combat function, genre-agnostic."""

    model_config = {"extra": "forbid"}

    id: str
    combat_function: str
    stat_affinity: list[str] = Field(default_factory=list)


class NpcRole(BaseModel):
    """NPC narrative role — assigned by the system, never player-facing."""

    model_config = {"extra": "forbid"}

    id: str
    narrative_function: str
    skip_enrichment: bool = False


class BaseArchetypes(BaseModel):
    """Top-level container for the base archetype definitions file."""

    model_config = {"extra": "forbid"}

    jungian: list[JungianArchetype] = Field(default_factory=list)
    rpg_roles: list[RpgRole] = Field(default_factory=list)
    npc_roles: list[NpcRole] = Field(default_factory=list)
