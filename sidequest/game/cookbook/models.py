"""Cookbook pydantic models — corpus rows, authored tables, manifest.

Mirrors the genre-layer convention (model_config extra=forbid). Field
names are the contract; later phases and oq-1 depend on them verbatim.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sidequest.protocol.models import LocationEntity

_FORBID = {"extra": "forbid"}


class CorpusMonster(BaseModel):
    model_config = _FORBID
    name: str
    size: str
    type: str
    tags: list[str] = Field(default_factory=list)
    alignment: str
    cr: float
    xp: int
    source: str = ""


class CorpusItem(BaseModel):
    model_config = _FORBID
    name: str
    item_type: str
    rarity: str
    attunement: bool = False
    notes: str = ""
    source: str = ""


class FilterClause(BaseModel):
    """One predicate term. All present fields must hold (AND)."""

    model_config = _FORBID
    type: str | None = None
    tags_any: list[str] | None = None
    name_glob: str | None = None


class RaceFilter(BaseModel):
    model_config = _FORBID
    any_of: list[FilterClause]


class RaceDeny(BaseModel):
    model_config = _FORBID
    name_glob: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class BigBadDecl(BaseModel):
    model_config = _FORBID
    name: str
    min_band: str


class RaceConcept(BaseModel):
    model_config = _FORBID
    framing: str
    sourced_from: str


class LootBias(BaseModel):
    model_config = _FORBID
    category_weight: dict[str, float] = Field(default_factory=dict)


class RaceDef(BaseModel):
    model_config = _FORBID
    id: str
    display: str
    filter: RaceFilter
    deny: RaceDeny = Field(default_factory=RaceDeny)
    telegraph: dict[str, str] = Field(default_factory=dict)
    loot_bias: LootBias = Field(default_factory=LootBias)
    big_bads: list[BigBadDecl] = Field(default_factory=list)
    concept: RaceConcept | None = None


class LookDef(BaseModel):
    model_config = _FORBID
    id: str
    generator_binding: str
    register: str
    dressing: list[str] = Field(default_factory=list)


class CrBand(BaseModel):
    model_config = _FORBID
    id: str
    depth_lt: float
    cr_min: float
    cr_max: float


class SizeBudget(BaseModel):
    model_config = _FORBID
    burst_lte: int
    wandering_rolls: int
    special_rooms: int
    loot_rolls: int


class BigBadGate(BaseModel):
    model_config = _FORBID
    on_first_band_entry: list[str]
    recurring_chance: dict[str, float]


class Affinities(BaseModel):
    model_config = _FORBID
    cr_bands: list[CrBand]
    big_bad_gate: BigBadGate
    look_race_affinity: dict[str, dict[str, float]]
    rarity_by_band: dict[str, dict[str, float]]
    size_by_burst: list[SizeBudget]
    big_bad_forces_size: str

    def band_order(self) -> dict[str, int]:
        """Ordinal index per spec §4.2 (shallow < mid < deep)."""
        return {b.id: i for i, b in enumerate(self.cr_bands)}


class SpecialRoom(BaseModel):
    model_config = _FORBID
    id: str
    telegraph: str
    mechanic: str
    outcome: str
    min_band: str
    feeds_setpiece_slot: bool = True


class Reskin(BaseModel):
    model_config = _FORBID
    mapping: dict[str, str] = Field(default_factory=dict)


class WorldRegister(BaseModel):
    model_config = _FORBID
    register: str
    allow_types: list[str]
    deny: RaceDeny = Field(default_factory=RaceDeny)
    humanoid_constraint: str = ""
    reskin: dict[str, str] = Field(default_factory=dict)
    marquee: list[str] = Field(default_factory=list)


class GeneratedRoomDescription(BaseModel):
    """Cookbook-composed room prose + manifest for ONE materialized region.

    Story 55-1 / ADR-109. Produced by ``compose_room_prose`` from
    ``LookDef.dressing`` lines and any attached ``SpecialRoom.telegraph``
    references; consumed by ``_stage_emit_room_yamls`` which writes the
    YAML to ``<world>/rooms/<room_id>.yaml``. All entities emitted by
    this path carry ``provenance="cookbook"`` — the seam ADR-100
    KnownFacts / Story 54-6 promotion logic uses to tell authored
    content from procedurally composed content.
    """

    model_config = _FORBID
    room_id: str = Field(min_length=1)
    description: str
    entities: list[LocationEntity] = Field(default_factory=list)


class RegionContentManifest(BaseModel):
    """The deterministic contract output oq-1's materializer consumes.

    Carries cr_band + raw corpus rows. CR→Edge translation is the
    oq-1 materializer seam (ADR-014/078) — NOT done here.

    Story 55-1 / ADR-109: ``room_descriptions`` carries the
    cookbook-composed ``(prose, entities[])`` tuple per region the
    materializer will write to ``<world>/rooms/<id>.yaml``.
    """

    model_config = _FORBID
    race: str
    cr_band: str
    size_budget: dict[str, int]
    wandering_table: list[dict]
    loot_table: list[dict]
    special_rooms: list[dict]
    big_bad: dict | None = None
    room_descriptions: list[GeneratedRoomDescription] = Field(default_factory=list)
