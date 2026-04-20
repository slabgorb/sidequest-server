"""NPC trait database — personality, physical, and behavioral quirks.

Port of sidequest-genre/src/models/npc_traits.rs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NpcTrait(BaseModel):
    """A single NPC trait entry with optional Jungian affinity weighting."""

    model_config = {"extra": "forbid"}

    trait_name: str = Field(alias="trait", serialization_alias="trait")
    jungian_affinity: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid", "populate_by_name": True}


class NpcTraitsDatabase(BaseModel):
    """Master NPC traits database loaded from npc_traits.yaml."""

    model_config = {"extra": "forbid"}

    personality: list[NpcTrait] = Field(default_factory=list)
    physical: list[NpcTrait] = Field(default_factory=list)
    behavioral: list[NpcTrait] = Field(default_factory=list)
