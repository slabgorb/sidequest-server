"""Genre-level archetype constraints from archetype_constraints.rs.

Port of sidequest-genre/src/models/archetype_constraints.rs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PairingWeight(StrEnum):
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

    def pairing_weight(self, jungian: str, rpg_role: str) -> PairingWeight | None:
        """Look up the weight of a [jungian, rpg_role] pairing.

        Returns None if the pairing is not listed in any weight category.
        Port of Rust ArchetypeConstraints::pairing_weight().
        """
        def matches(pair: list[str]) -> bool:
            return len(pair) == 2 and pair[0] == jungian and pair[1] == rpg_role

        if any(matches(p) for p in self.valid_pairings.common):
            return PairingWeight.common
        if any(matches(p) for p in self.valid_pairings.uncommon):
            return PairingWeight.uncommon
        if any(matches(p) for p in self.valid_pairings.rare):
            return PairingWeight.rare
        if any(matches(p) for p in self.valid_pairings.forbidden):
            return PairingWeight.forbidden
        return None

    def fallback_name(self, rpg_role: str) -> str | None:
        """Get the fallback name for an RPG role in this genre.

        Returns None if no genre flavor is registered for the given role id.
        Port of Rust ArchetypeConstraints::fallback_name().
        """
        flavor = self.genre_flavor.rpg_roles.get(rpg_role)
        if flavor is not None:
            return flavor.fallback_name
        return None
