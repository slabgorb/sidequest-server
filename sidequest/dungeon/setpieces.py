"""Set-piece TEMPLATE schema (spec: Beneath Sünden §4, §6; §10 step 4).

A set-piece is a template with randomized component slots. Plan 4 ships
the SCHEMA ONLY — Plan 6 extends THIS SAME MODULE with the seeded roll +
trope/quest attach + ledger wiring (a real type now, roll/attach methods
later — identical to Plan 3's DepthReport precedent; NOT a stub).

`telegraph` + `outcome` are mandatory and non-blank: spec §4 requires
every set-piece to carry the tell a careful party can read AND a hard,
legible outcome ("the dungeon plays fair"). `save_or_die` is INERT
reference data — ADR-074's existing player-facing dice protocol consumes
it at Plan-6 attach; spec §4 forbids a new mechanics engine, so nothing
here resolves a roll.

trope/quest components are validated STRUCTURALLY only (non-blank id +
free params). Cross-resolution against tropes.yaml / scenario is Plan 6
attach (spec §6: encounters.rb is reference-only, not ported).

Honest deferral (Plan 2/3 precedent): no runtime/session/OTEL consumer
in Plan 4 — Plan 6 rolls set-pieces / starts trope+quest components /
writes the ledger; Plan 7's materializer drives it. Proven not-a-stub
by tests/dungeon/test_themes_wiring.py (Task 7) loading the real shipped
scaffold.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _nonblank(v: str) -> str:
    if not v.strip():
        raise ValueError("must be a non-blank string")
    return v


class SlotOption(BaseModel):
    """One weighted candidate for a component slot. The seeded roll that
    picks among options is Plan 6 — here `weight` is validated > 0 only."""

    model_config = ConfigDict(extra="forbid")

    value: str
    weight: float = 1.0

    @field_validator("value")
    @classmethod
    def _v_value(cls, v: str) -> str:
        return _nonblank(v)

    @field_validator("weight")
    @classmethod
    def _v_weight(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("weight must be > 0")
        return v


class ComponentSlot(BaseModel):
    """A named slot (layout|features|creatures|loot) with >=1 option."""

    model_config = ConfigDict(extra="forbid")

    name: str
    options: list[SlotOption]

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _nonblank(v)

    @field_validator("options")
    @classmethod
    def _v_options(cls, v: list[SlotOption]) -> list[SlotOption]:
        if not v:
            raise ValueError("a component slot needs at least one option")
        return v


class TropeComponent(BaseModel):
    """Reference to a trope that Plan 6 will START at attach. Plan 4 only
    checks the id is non-blank; resolution vs tropes.yaml is Plan 6."""

    model_config = ConfigDict(extra="forbid")

    trope_id: str
    params: dict = Field(default_factory=dict)

    @field_validator("trope_id")
    @classmethod
    def _v_id(cls, v: str) -> str:
        return _nonblank(v)


class QuestComponent(BaseModel):
    """Reference to a quest that Plan 6 will SEED at attach. Structural
    validation only here (non-blank id)."""

    model_config = ConfigDict(extra="forbid")

    quest_id: str
    params: dict = Field(default_factory=dict)

    @field_validator("quest_id")
    @classmethod
    def _v_id(cls, v: str) -> str:
        return _nonblank(v)


class DepthBand(BaseModel):
    """Raw depth_score eligibility window (Plan 3 units, depth_per_hop=10).

    NOT player-facing level buckets — depth_score is the authoritative
    gradient; "level" is never an authoritative key (spec §5). `max=None`
    means unbounded-deep (eligible arbitrarily far down)."""

    model_config = ConfigDict(extra="forbid")

    min: float = 0.0
    max: float | None = None

    @field_validator("min")
    @classmethod
    def _v_min(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError("depth_band.min must be >= 0")
        return v

    @model_validator(mode="after")
    def _v_band(self) -> DepthBand:
        if self.max is not None and self.max < self.min:
            raise ValueError("depth_band.max must be >= depth_band min")
        return self


class SaveOrDie(BaseModel):
    """INERT reference data for ADR-074's existing dice protocol — Plan 6
    feeds this to the player-facing roll. Spec §4: no new mechanics
    engine; nothing here resolves anything."""

    model_config = ConfigDict(extra="forbid")

    save: str
    dc: int

    @field_validator("save")
    @classmethod
    def _v_save(cls, v: str) -> str:
        return _nonblank(v)

    @field_validator("dc")
    @classmethod
    def _v_dc(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("dc must be > 0")
        return v


class SetPiece(BaseModel):
    """An authored, telegraphed, lethal set-piece TEMPLATE (Tomb of
    Horrors, spec §4). Plan 4 = schema; Plan 6 rolls slots / starts trope
    & quest components / writes the ledger."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    telegraph: str
    outcome: str
    depth_band: DepthBand = Field(default_factory=DepthBand)
    save_or_die: SaveOrDie | None = None
    slots: list[ComponentSlot] = Field(default_factory=list)
    trope_components: list[TropeComponent] = Field(default_factory=list)
    quest_components: list[QuestComponent] = Field(default_factory=list)

    @field_validator("id", "name", "telegraph", "outcome")
    @classmethod
    def _v_text(cls, v: str) -> str:
        return _nonblank(v)

    @model_validator(mode="after")
    def _v_unique_slots(self) -> SetPiece:
        names = [s.name for s in self.slots]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate component slot name(s): {sorted(dupes)}")
        return self
