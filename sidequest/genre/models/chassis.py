"""Genre-layer chassis catalog pydantic models.

Mirrors `chassis_classes.yaml` shape per docs/design/rig-taxonomy.md.
Slice scope: only fields used by Coyote Reach's voidborn_freighter +
the_tea_brew. Hardpoints, chassis_death, full provenance vocabulary
are deferred to follow-on specs.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CrewModel = Literal["single_pilot", "strict_roles", "flexible_roles"]
EmbodimentModel = Literal["singular", "crew_only", "ancillary", "swarm"]
CrewAwareness = Literal["none", "surface", "biometric", "interior", "total"]
ScaleBand = Literal[
    "personal", "vehicular", "capital_ship", "station_class"
]
BondTier = Literal[
    "severed", "hostile", "strained", "neutral",
    "familiar", "trusted", "fused",
]


class ChassisVoiceSpec(BaseModel):
    model_config = {"extra": "forbid"}
    default_register: str
    vocal_tics: list[str] = Field(default_factory=list)
    silence_register: str | None = None
    name_forms_by_bond_tier: dict[BondTier, str]


class PsiResonanceSpec(BaseModel):
    model_config = {"extra": "forbid"}
    default: Literal["receptive", "dampening", "neutral", "incomprehensible"]
    amplifies: list[str] = Field(default_factory=list)


class InteriorRoomSpec(BaseModel):
    model_config = {"extra": "forbid"}
    id: str
    display_name: str
    narrative_register: str | None = None
    default_occupants: list[str] = Field(default_factory=list)
    bond_eligible_for: list[str] = Field(default_factory=list)


class CrewRoleSpec(BaseModel):
    model_config = {"extra": "forbid"}
    id: str
    operates_hardpoints: str | list[str] = "*"
    bond_eligible: bool = False
    default_seat: str | None = None


class ChassisClass(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}
    id: str
    display_name: str
    # Field name conflict with python keyword: alias.
    chassis_class: str = Field(alias="class")
    provenance: str
    scale_band: ScaleBand
    crew_model: CrewModel
    embodiment_model: EmbodimentModel = "singular"
    crew_awareness: CrewAwareness = "none"
    psi_resonance: PsiResonanceSpec | None = None
    default_voice: ChassisVoiceSpec | None = None
    interior_rooms: list[InteriorRoomSpec] = Field(default_factory=list)
    crew_roles: list[CrewRoleSpec] = Field(default_factory=list)
    # Deferred per slice: hardpoints, chassis_death, ancillary_*.


class ChassisClassesConfig(BaseModel):
    model_config = {"extra": "forbid"}
    version: str
    genre: str
    classes: list[ChassisClass]
