"""World-level archetype funnel structs.

Port of sidequest-genre/src/models/archetype_funnels.rs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Funnel(BaseModel):
    """A single funnel entry — maps multiple axis combinations to one named archetype."""

    model_config = {"extra": "forbid"}

    name: str
    absorbs: list[list[str]] = Field(default_factory=list)
    faction: str | None = None
    lore: str
    cultural_status: str | None = None
    disposition_toward: dict[str, str] = Field(default_factory=dict)


class WorldConstraints(BaseModel):
    """World-level additional constraints."""

    model_config = {"extra": "forbid"}

    forbidden: list[list[str]] = Field(default_factory=list)


class ArchetypeFunnels(BaseModel):
    """World-level archetype funnels — resolves axis pairs to named archetypes."""

    model_config = {"extra": "forbid"}

    funnels: list[Funnel] = Field(default_factory=list)
    additional_constraints: WorldConstraints = Field(default_factory=WorldConstraints)
