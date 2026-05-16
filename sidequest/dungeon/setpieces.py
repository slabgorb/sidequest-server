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

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
