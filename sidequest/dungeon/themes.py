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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sidequest.dungeon.interiors import ALGORITHMS


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
