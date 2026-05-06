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
    # World-flavor metadata — no engine consumer. caverns_sunden tags
    # each funnel with the sin its culture is aligned to ("pride",
    # "greed", "gluttony"); other worlds may add their own descriptive
    # tags here. Optional and free-form.
    sin_origin: str | None = None


class WorldConstraints(BaseModel):
    """World-level additional constraints."""

    model_config = {"extra": "forbid"}

    forbidden: list[list[str]] = Field(default_factory=list)


class ArchetypeFunnels(BaseModel):
    """World-level archetype funnels — resolves axis pairs to named archetypes."""

    model_config = {"extra": "forbid"}

    funnels: list[Funnel] = Field(default_factory=list)
    additional_constraints: WorldConstraints = Field(default_factory=WorldConstraints)

    def resolve(self, jungian: str, rpg_role: str) -> Funnel | None:
        """Resolve a [jungian, rpg_role] pair to a funnel entry.

        Returns None if no funnel claims this combination.
        Port of Rust ArchetypeFunnels::resolve().
        """
        for funnel in self.funnels:
            if any(
                len(pair) == 2 and pair[0] == jungian and pair[1] == rpg_role
                for pair in funnel.absorbs
            ):
                return funnel
        return None

    def is_forbidden(self, jungian: str, rpg_role: str) -> bool:
        """Check if a pairing is forbidden at the world level.

        Port of Rust ArchetypeFunnels::is_forbidden().
        """
        return any(
            len(pair) == 2 and pair[0] == jungian and pair[1] == rpg_role
            for pair in self.additional_constraints.forbidden
        )
