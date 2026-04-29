"""World-layer rigs.yaml pydantic.

Each chassis instance picks a class from the genre's chassis_classes.yaml
and adds named state. Resolution into a runtime ChassisInstance happens
at world-load (sidequest/game/world_materialization.py).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from sidequest.genre.models.chassis import BondTier, ChassisVoiceSpec


class OceanScores(BaseModel):
    model_config = {"extra": "forbid"}
    O: float = 0.5  # noqa: E741
    C: float = 0.5
    E: float = 0.5
    A: float = 0.5
    N: float = 0.5


class BondSeed(BaseModel):
    model_config = {"extra": "forbid"}
    character_role: str
    bond_strength_character_to_chassis: float = 0.0
    bond_strength_chassis_to_character: float = 0.0
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
