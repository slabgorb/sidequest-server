"""Spell catalog loader — reads spells/<tradition>_l<n>.yaml from a genre pack.

Each catalog file is a list of spells at one tradition+level. Plugins consume
the catalog to validate cast workings and to render spell metadata in the
narrator context block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class SpellSave(BaseModel):
    model_config = {"extra": "forbid"}
    stat: str | None
    effect: Literal["none", "negates", "halves"] | str  # str allows partial:<text>


class SpellComponents(BaseModel):
    model_config = {"extra": "forbid"}
    verbal: bool = False
    somatic: bool = False
    material: str | None = None


class SpellReverse(BaseModel):
    """Cleric reversed-spell variant. Mage spells leave this None."""

    model_config = {"extra": "forbid"}
    id: str
    effect_template: str
    narrator_register: str
    domain: str


class Spell(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    name: str
    level: int
    tradition: Literal["arcane", "divine"]
    range: Literal["touch", "close", "near", "far", "unlimited"]
    target: Literal["single", "area", "self", "object"]
    duration: str  # "instant" | "until_rest" | "turns:<N|XdY>" | "permanent"
    save: SpellSave
    effect_template: str
    components: SpellComponents
    backlash: str | None
    narrator_register: str
    hard_limits_check: list[str] = Field(default_factory=list)
    domain: str
    otel_attrs: list[str] = Field(default_factory=list)
    reverse: SpellReverse | None = None


class SpellCatalog(BaseModel):
    model_config = {"extra": "forbid"}

    version: str
    genre: str
    tradition: Literal["arcane", "divine"]
    level: int
    spells: list[Spell]

    def get(self, spell_id: str) -> Spell:
        for s in self.spells:
            if s.id == spell_id:
                return s
        raise KeyError(f"spell {spell_id!r} not in catalog (have: {[s.id for s in self.spells]})")


def load_spell_catalog(path: Path) -> SpellCatalog:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SpellCatalog.model_validate(raw)
