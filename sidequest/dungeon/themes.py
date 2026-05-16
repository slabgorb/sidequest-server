"""Theme palette schema + strict loader (spec: Beneath Sünden §5.2, §6;
§10 step 4).

The pack ships a curated `themes/` directory; each theme keys an interior
generator, declares its depth_score eligibility band (Plan 3 raw units —
NOT player-facing level buckets, spec §5), creature/loot tables, narrator
register, adjacency affinities, and a set-piece library.

Loader is STANDALONE and fail-loud (CLAUDE.md No Silent Fallbacks):
deliberately NOT wired into the generic load_genre_pack — a `themes/` dir
is dungeon-specific to beneath_sunden; an optional generic loader would
silently no-op for the other 5 packs. The runtime consumer (Plan 7's
materializer building a depth-filtered theme_pool + Plan 6's set-piece
roll) is an honest deferral, identical to Plan 2/3's stance — proven by
tests/dungeon/test_themes_wiring.py loading the REAL shipped scaffold.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sidequest.dungeon.interiors import ALGORITHMS
from sidequest.dungeon.setpieces import SetPiece


class InteriorSpec(BaseModel):
    """Which ported maze-maker generator fills this theme's interiors.

    `algorithm` is validated against the REAL Plan-1 coordinator registry
    (`interiors.ALGORITHMS`) — a genuine cross-module wire, not a copied
    enum. `braid_ratio` is range-checked here; `generate_interior` itself
    skips the braid post-process when it is <= 0.0 (spec §5.2: labyrinth-
    trap stays a pristine perfect maze at 0.0)."""

    model_config = ConfigDict(extra="forbid")

    algorithm: str
    params: dict = Field(default_factory=dict)
    braid_ratio: float = 0.0

    @field_validator("algorithm")
    @classmethod
    def _v_algorithm(cls, v: str) -> str:
        if v not in ALGORITHMS:
            raise ValueError(
                f"unknown interior algorithm {v!r}; "
                f"known: {sorted(ALGORITHMS)}"
            )
        return v

    @field_validator("braid_ratio")
    @classmethod
    def _v_braid(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("braid_ratio must be in [0.0, 1.0]")
        return v


# spec §5.2 — theme class -> generator family (hard invariant)
_CLASS_ALGORITHM = {
    "organic": "cellular",
    "labyrinthine": "depthfirst",
    "structured": "prim",
    "built": "roomcorridor",
}


def _nonblank(v: str) -> str:
    if not v.strip():
        raise ValueError("must be a non-blank string")
    return v


class DepthBand(BaseModel):
    """Raw depth_score eligibility window (Plan 3 units). max=None ->
    unbounded-deep. NOT player-facing level buckets (spec §5)."""

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


class NarratorFlavor(BaseModel):
    """Register + flavor seed for Plan 7's prompt assembly. Beneath
    Sünden plays grave/lethal (spec §3) — register & flavor non-blank."""

    model_config = ConfigDict(extra="forbid")

    register: str  # spec §6 name; shadows ABCMeta.register via metaclass — known, non-breaking, do not rename without spec sign-off
    flavor: str
    motifs: list[str] = Field(default_factory=list)

    @field_validator("register", "flavor")
    @classmethod
    def _v_text(cls, v: str) -> str:
        return _nonblank(v)

    @field_validator("motifs")
    @classmethod
    def _v_motifs(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.strip():
                raise ValueError("a motif cannot be blank")
        return v


class Adjacency(BaseModel):
    """Theme-placement affinities (spec §6: 'tomb -> crypt deepens;
    flooded clusters'). Palette-level cross-resolution (ids must exist,
    no self-avoidance against the OWNING id) is enforced in
    load_theme_palette (Task 5); here we reject the trivially-nonsensical
    forms detectable without the owning id."""

    model_config = ConfigDict(extra="forbid")

    prefers: list[str] = Field(default_factory=list)
    avoids: list[str] = Field(default_factory=list)

    @field_validator("prefers")
    @classmethod
    def _v_prefers(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.strip():
                raise ValueError("a prefers entry cannot be a blank id")
        return v

    @field_validator("avoids")
    @classmethod
    def _v_avoids(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.strip():
                raise ValueError(
                    "a theme cannot avoid itself / a blank id "
                    "(empty avoids entry is nonsensical)"
                )
        return v

    @model_validator(mode="after")
    def _v_disjoint(self) -> Adjacency:
        both = set(self.prefers) & set(self.avoids)
        if both:
            raise ValueError(
                f"theme id(s) in both prefers and avoids: {sorted(both)}"
            )
        return self


class CreatureEntry(BaseModel):
    """Weighted creature ref. Resolution vs monster manual is Plan 6."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    weight: float = 1.0
    depth_band: DepthBand | None = None

    @field_validator("ref")
    @classmethod
    def _v_ref(cls, v: str) -> str:
        return _nonblank(v)

    @field_validator("weight")
    @classmethod
    def _v_weight(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("weight must be > 0")
        return v


class LootEntry(BaseModel):
    """Weighted loot ref. Resolution vs inventory.yaml is Plan 6."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    weight: float = 1.0
    depth_band: DepthBand | None = None

    @field_validator("ref")
    @classmethod
    def _v_ref(cls, v: str) -> str:
        return _nonblank(v)

    @field_validator("weight")
    @classmethod
    def _v_weight(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("weight must be > 0")
        return v


class DungeonTheme(BaseModel):
    """One curated themed zone definition (spec §6)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    generator_class: str
    interior: InteriorSpec
    depth_band: DepthBand
    narrator: NarratorFlavor
    adjacency: Adjacency = Field(default_factory=Adjacency)
    creature_table: list[CreatureEntry] = Field(default_factory=list)
    loot_table: list[LootEntry] = Field(default_factory=list)
    set_pieces: list[SetPiece] = Field(default_factory=list)

    @field_validator("id", "display_name")
    @classmethod
    def _v_text(cls, v: str) -> str:
        return _nonblank(v)

    @field_validator("generator_class")
    @classmethod
    def _v_class(cls, v: str) -> str:
        if v not in _CLASS_ALGORITHM:
            raise ValueError(
                f"unknown generator_class {v!r}; "
                f"known: {sorted(_CLASS_ALGORITHM)}"
            )
        return v

    @model_validator(mode="after")
    def _v_class_matches_algorithm(self) -> DungeonTheme:
        expected = _CLASS_ALGORITHM[self.generator_class]
        if self.interior.algorithm != expected:
            raise ValueError(
                f"generator_class {self.generator_class!r} does not match "
                f"interior.algorithm {self.interior.algorithm!r} "
                f"(spec §5.2 expects {expected!r})"
            )
        return self
