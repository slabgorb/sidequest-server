"""Genre-level archetype constraints from archetype_constraints.rs.

Port of sidequest-genre/src/models/archetype_constraints.rs.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PairingWeight(str, Enum):
    """Weight classification for a Jungian x RPG Role pairing."""

    common = "common"
    uncommon = "uncommon"
    rare = "rare"
    forbidden = "forbidden"


class ValidPairings(BaseModel):
    """Valid pairings grouped by weight. Each entry is [jungian_id, rpg_role_id]."""

    model_config = {"extra": "forbid"}

    common: list[list[str]] = Field(default_factory=list)
    uncommon: list[list[str]] = Field(default_factory=list)
    rare: list[list[str]] = Field(default_factory=list)
    forbidden: list[list[str]] = Field(default_factory=list)


class JungianFlavor(BaseModel):
    """Genre-specific flavor for a Jungian archetype."""

    model_config = {"extra": "forbid"}

    speech_pattern: str = ""
    equipment_tendency: str = ""
    visual_cues: str = ""


class RpgRoleFlavor(BaseModel):
    """Genre-specific flavor for an RPG role."""

    model_config = {"extra": "forbid"}

    fallback_name: str


class GenreFlavor(BaseModel):
    """Genre-level flavor collections."""

    model_config = {"extra": "forbid"}

    jungian: dict[str, JungianFlavor] = Field(default_factory=dict)
    rpg_roles: dict[str, RpgRoleFlavor] = Field(default_factory=dict)


class ArchetypeConstraints(BaseModel):
    """Genre-level archetype constraints — valid pairings and flavor."""

    model_config = {"extra": "forbid"}

    valid_pairings: ValidPairings
    genre_flavor: GenreFlavor
    npc_roles_available: list[str] = Field(default_factory=list)
