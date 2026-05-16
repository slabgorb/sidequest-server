"""Load a beneath_sunden world dir into a typed CookbookBundle.

This is the COOKBOOK loader (oq-2). It is NOT oq-1's region_graph/themes
loader (spec §2) — distinct concern, distinct file. No silent fallback:
a missing required file raises FileNotFoundError naming the path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sidequest.game.cookbook.corpus import resolve_race
from sidequest.game.cookbook.curation import apply_world_register
from sidequest.game.cookbook.models import (
    Affinities,
    BigBadDecl,
    CorpusItem,
    CorpusMonster,
    LookDef,
    RaceDef,
    SpecialRoom,
    WorldRegister,
)


@dataclass(frozen=True)
class CookbookBundle:
    monsters: list[CorpusMonster]
    items: list[CorpusItem]
    register: WorldRegister
    races: list[RaceDef]
    looks: list[LookDef]
    affinities: Affinities
    specials: list[SpecialRoom]


def _load_yaml(path: Path) -> object:
    if not path.exists():
        raise FileNotFoundError(f"cookbook: required file missing: {path}")
    return yaml.safe_load(path.read_text())


def load_cookbook(world: Path) -> CookbookBundle:
    cp = world / "corpus"
    cb = world / "cookbook"
    monsters = [CorpusMonster(**r) for r in _load_yaml(cp / "monsters.yaml")]
    items = [CorpusItem(**r) for r in _load_yaml(cp / "items.yaml")]
    register = WorldRegister(**_load_yaml(world / "world_register.yaml"))
    race_dir = cb / "races"
    if not race_dir.is_dir():
        raise FileNotFoundError(f"cookbook: races dir missing: {race_dir}")
    races = [RaceDef(**_load_yaml(p)) for p in sorted(race_dir.glob("*.yaml"))]
    looks = [LookDef(**look) for look in _load_yaml(cb / "looks.yaml")["looks"]]
    affinities = Affinities(**_load_yaml(cb / "affinities.yaml"))
    specials = [SpecialRoom(**s) for s in _load_yaml(cb / "special_rooms.yaml")["special_rooms"]]
    return CookbookBundle(
        monsters=monsters,
        items=items,
        register=register,
        races=races,
        looks=looks,
        affinities=affinities,
        specials=specials,
    )


class CookbookValidationError(RuntimeError):
    """Loud build-time failure (spec §7) — never a silent fallback."""


def validate_bundle(bundle: CookbookBundle) -> None:
    """Spec §7 gates, corrected per "Data-Forced Design Item".

    A RACE must resolve ≥1 curated row in (a) the SHALLOW band (entry
    guarantee) and (b) the declared min_band of each of its big_bads (a
    declared capstone tier must be non-empty where it is declared to
    begin). Bands a RACE cannot fill — including bands ABOVE a big_bad's
    min_band, e.g. ooze (CR ceiling 4) cannot fill `deep` — are NOT a
    build error: the assembler re-rolls observably (cookbook.race.reroll,
    Task 14/18). The plan's framing line said "every band ≥ min_band",
    which contradicts the Data-Forced decision and the must-pass
    real-bundle test; the min_band-only check is the authoritative
    resolution (recorded in the Task 23 spec-status note). Raises
    CookbookValidationError naming the offender. No silent fallback.
    """
    curated = apply_world_register(bundle.monsters, bundle.register)
    band_order = bundle.affinities.band_order()
    band_by_id = {b.id: b for b in bundle.affinities.cr_bands}
    shallow_id = bundle.affinities.cr_bands[0].id

    def _resolves(race, band_id: str) -> bool:
        b = band_by_id[band_id]
        return bool(resolve_race(curated, race, cr_min=b.cr_min, cr_max=b.cr_max))

    for race in bundle.races:
        # (a) entry guarantee — every faction encounterable at shallow.
        if not _resolves(race, shallow_id):
            # Surface the denied types/tags so the error names the cause,
            # not just the victim RACE (spec §7: loud, actionable failure).
            filter_types = {c.type for c in race.filter.any_of if c.type}
            blocked = sorted(t.lower() for t in filter_types if t in bundle.register.deny.types)
            cause = f" (world_register denies type(s): {', '.join(blocked)})" if blocked else ""
            raise CookbookValidationError(
                f"RACE '{race.id}' resolves to ZERO curated rows at the "
                f"entry band '{shallow_id}'{cause}. Widen the filter or "
                f"relax world_register.deny — every faction must be "
                f"encounterable."
            )
        # (b) declared capstones must be reachable at their own min_band.
        # "Data-Forced Design Item": a RACE that cannot fill bands ABOVE
        # min_band is NOT an error — the assembler re-rolls observably.
        # The error is a big_bad whose CR puts it outside its min_band
        # (e.g. Goblin CR 0.25 declared at min_band 'deep' cr_min=6).
        for _bb in race.big_bads:
            bb: BigBadDecl = BigBadDecl(**_bb) if isinstance(_bb, dict) else _bb
            if bb.min_band not in band_order:
                raise CookbookValidationError(
                    f"RACE '{race.id}' big_bad '{bb.name}' has unknown min_band '{bb.min_band}'"
                )
            if not any(m.name == bb.name for m in curated):
                raise CookbookValidationError(
                    f"RACE '{race.id}' big_bad '{bb.name}' does not resolve "
                    f"in curated corpus/monsters.yaml"
                )
            if not _resolves(race, bb.min_band):
                raise CookbookValidationError(
                    f"RACE '{race.id}' declares big_bad '{bb.name}' at "
                    f"min_band '{bb.min_band}' but resolves ZERO rows at "
                    f"that band — the capstone is unreachable. Lower "
                    f"min_band or widen the filter."
                )

    # Every LOOK must have ≥1 affinity RACE that can fill shallow, else a
    # region under that LOOK could exhaust the re-roll (spec §7: no
    # silent empty table — fail at build instead).
    for look, weights in bundle.affinities.look_race_affinity.items():
        by_id = {r.id: r for r in bundle.races}
        if not any(
            w > 0 and rid in by_id and _resolves(by_id[rid], shallow_id)
            for rid, w in weights.items()
        ):
            raise CookbookValidationError(
                f"LOOK '{look}' has no affinity RACE that resolves at "
                f"'{shallow_id}' — every region under it would fail."
            )

    # reskin keys must resolve in the raw corpus (spec §7).
    names = {m.name for m in bundle.monsters}
    for key in bundle.register.reskin:
        if key not in names:
            raise CookbookValidationError(
                f"world_register.reskin key '{key}' not in corpus/monsters.yaml"
            )
