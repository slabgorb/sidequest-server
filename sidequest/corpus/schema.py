from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CORPUS_SCHEMA_VERSION: Literal[1] = 1


class DisputeTag(StrEnum):
    MIS_RESOLVED_REFERENT = "mis_resolved_referent"
    INVENTED_NPC = "invented_npc"
    SOFTENED_LETHALITY = "softened_lethality"
    GM_OVERRIDE = "gm_override"


class MineProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_save: str
    event_seq: int | None


class TrainingPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    genre: str
    world: str
    round_number: int = Field(ge=0)
    input_text: str = Field(min_length=1)
    output_text: str = Field(min_length=1)
    provenance: MineProvenance


class LabeledPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pair: TrainingPair
    disputes: list[DisputeTag]
    corrected_output: str
    labeler: str
