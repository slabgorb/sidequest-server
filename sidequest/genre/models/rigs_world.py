"""World-layer rigs.yaml pydantic.

Each chassis instance picks a class from the genre's chassis_classes.yaml
and adds named state. Resolution into a runtime ChassisInstance happens
at world-load (sidequest/game/world_materialization.py).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from sidequest.genre.models.chassis import BondTier, ChassisVoiceSpec


class OceanScores(BaseModel):
    """Chassis OCEAN — single-letter keys, 0.0–1.0 scale.

    Distinct from `OceanProfile` (genre.models.ocean) which uses full
    field names on a 0.0–10.0 scale for character OCEAN. The rig taxonomy
    (docs/design/rig-taxonomy.md) authored chassis OCEAN at this scale
    and shape; merging the two representations is deferred and tracked
    as an open issue in the slice spec.
    """

    model_config = {"extra": "forbid"}
    O: float = Field(default=0.5, ge=0.0, le=1.0)  # noqa: E741
    C: float = Field(default=0.5, ge=0.0, le=1.0)
    E: float = Field(default=0.5, ge=0.0, le=1.0)
    A: float = Field(default=0.5, ge=0.0, le=1.0)
    N: float = Field(default=0.5, ge=0.0, le=1.0)


class BondSeed(BaseModel):
    model_config = {"extra": "forbid"}
    character_role: str
    bond_strength_character_to_chassis: float = Field(default=0.0, ge=-1.0, le=1.0)
    bond_strength_chassis_to_character: float = Field(default=0.0, ge=-1.0, le=1.0)
    bond_tier_character: BondTier = "neutral"
    bond_tier_chassis: BondTier = "neutral"
    history_seeds: list[str] = Field(default_factory=list)


class ChassisInstanceConfig(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}
    id: str
    name: str
    chassis_class_id: str = Field(alias="class")
    OCEAN: OceanScores = Field(default_factory=OceanScores)
    voice: ChassisVoiceSpec | None = None
    interior_rooms: list[str] = Field(default_factory=list)
    bond_seeds: list[BondSeed] = Field(default_factory=list)


class RigsWorldConfig(BaseModel):
    model_config = {"extra": "forbid"}
    version: str
    world: str
    genre: str
    chassis_instances: list[ChassisInstanceConfig]
