"""Spell catalog loader — reads spells/<tradition>_l<n>.yaml from a genre pack.

Each catalog file is a list of spells at one tradition+level. Plugins consume
the catalog to validate cast workings and to render spell metadata in the
narrator context block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_SPELL_SAVE_EFFECT_FIXED = frozenset({"none", "negates", "halves"})


def _save_category_default() -> str:
    """Lazy default factory — defers SaveCategory import to avoid circular import.

    sidequest.genre.models.rules → sidequest.game.__init__ →
    sidequest.game.persistence → sidequest.telemetry.spans.magic →
    sidequest.magic.models → sidequest.magic.spell_catalog (partially loaded).
    By the time this factory is *called* (at SpellSave instantiation), all
    modules are fully loaded, so the import is safe.
    """
    from sidequest.genre.models.rules import SaveCategory

    return SaveCategory.rods_staves_spells


class SpellSave(BaseModel):
    model_config = {"extra": "forbid"}
    stat: str | None
    # Allowed values: one of {"none", "negates", "halves"} OR a discriminated
    # "partial:<text>" form. The plain `str` is intentional only to admit the
    # partial: prefix; arbitrary strings (including typos like "negate") are
    # rejected by the validator below.
    effect: str
    # Which B/X B26 save column this spell consults. Defaults to the catch-all
    # magic column; per-spell override for ray-type or stone-type variants.
    # Annotated Any + default_factory (lazy import) to avoid a circular import
    # at class definition time. The _coerce_category validator enforces the
    # actual SaveCategory constraint. See _save_category_default docstring.
    category: Any = Field(default_factory=_save_category_default)

    @field_validator("effect")
    @classmethod
    def _validate_effect(cls, v: str) -> str:
        if v in _SPELL_SAVE_EFFECT_FIXED:
            return v
        if v.startswith("partial:") and len(v) > len("partial:"):
            return v
        raise ValueError(
            f"SpellSave.effect={v!r} is not a known value; expected one of "
            f"{sorted(_SPELL_SAVE_EFFECT_FIXED)} or a 'partial:<text>' discriminated form"
        )

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v: object) -> object:
        """Coerce a str value to a SaveCategory enum member (lazy import)."""
        if isinstance(v, str):
            from sidequest.genre.models.rules import SaveCategory

            try:
                return SaveCategory(v)
            except ValueError:
                valid = sorted(c.value for c in SaveCategory)
                raise ValueError(
                    f"SpellSave.category={v!r} is not a valid SaveCategory; expected one of {valid}"
                ) from None
        return v

    @model_validator(mode="after")
    def _validate_dragon_breath_no_stat(self) -> SpellSave:
        from sidequest.genre.models.rules import SaveCategory  # lazy — avoids circular import

        if self.category is SaveCategory.dragon_breath and self.stat is not None:
            raise ValueError(
                f"SpellSave with category=dragon_breath must have stat=None "
                f"(B/X B7: WIS does not modify Dragon Breath saves), got stat={self.stat!r}"
            )
        return self

    @model_validator(mode="after")
    def _validate_null_stat_coherence(self) -> SpellSave:
        # Story 47-10 codified rule (2026-05-09): save.stat: null means the
        # spell auto-applies (no opposed check). Pairing null-stat with a
        # non-none save.effect is contradictory authoring — there is no
        # save for the defender to "halve" or "negate" or partially resist
        # when the cast unconditionally lands. Reject at load time so the
        # author sees the inconsistency before runtime.
        #
        # Exempt: dragon_breath. Per B/X B7, Dragon Breath saves have
        # stat=None (no WIS modifier) but a real save vs. the table target;
        # effect="halves" on a dragon_breath save is canonical.
        from sidequest.genre.models.rules import SaveCategory  # lazy

        if self.category is SaveCategory.dragon_breath:
            return self
        if self.stat is None and self.effect != "none":
            raise ValueError(
                f"SpellSave.stat is None (auto-apply) but effect={self.effect!r}; "
                f"null-stat spells must declare effect='none' (there is no save "
                f"for the defender to react to). Either add a save.stat or set "
                f"effect to 'none'."
            )
        return self


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
    requires_mind: bool = False
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

    @model_validator(mode="after")
    def _check_unique_spell_ids(self) -> SpellCatalog:
        seen: dict[str, int] = {}
        for s in self.spells:
            seen[s.id] = seen.get(s.id, 0) + 1
        dupes = sorted(sid for sid, n in seen.items() if n > 1)
        if dupes:
            raise ValueError(
                f"SpellCatalog has duplicate spell ids: {dupes} "
                f"(tradition={self.tradition} level={self.level} genre={self.genre})"
            )
        return self

    def get(self, spell_id: str) -> Spell:
        for s in self.spells:
            if s.id == spell_id:
                return s
        raise KeyError(f"spell {spell_id!r} not in catalog (have: {[s.id for s in self.spells]})")


def load_spell_catalog(path: Path) -> SpellCatalog:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SpellCatalog.model_validate(raw)
