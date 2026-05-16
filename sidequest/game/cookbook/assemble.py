"""assemble_region — the deterministic region-content contract.

Spec §4.3: a pure function oq-1's materializer invokes. All randomness
derives ONLY from (campaign_seed, expansion_id) per Sünden Deep §11.
NO CR→Edge translation here (oq-1 materializer seam, ADR-014/078).
"""

from __future__ import annotations

import hashlib
import random

from sidequest.game.cookbook.corpus import resolve_race
from sidequest.game.cookbook.curation import apply_world_register
from sidequest.game.cookbook.loader import CookbookBundle
from sidequest.game.cookbook.models import Affinities, CrBand, RaceDef, SizeBudget


def region_rng(campaign_seed: str, expansion_id: str) -> random.Random:
    """A Random seeded purely by (campaign_seed, expansion_id)."""
    digest = hashlib.sha256(f"{campaign_seed}\x1f{expansion_id}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def band_for_depth(aff: Affinities, depth_score: float) -> CrBand:
    """First band whose depth_lt strictly exceeds depth_score.

    Bands are listed in increasing depth (spec §4.2). depth_lt is an
    exclusive upper bound; the last band's depth_lt (1.01) is the cap.
    """
    for band in aff.cr_bands:
        if depth_score < band.depth_lt:
            return band
    return aff.cr_bands[-1]


def budget_for_burst(aff: Affinities, burst_magnitude: int) -> SizeBudget:
    """First size_by_burst row whose burst_lte ≥ burst; else the largest.

    size_by_burst is listed in increasing burst (spec §4.2).
    """
    for row in aff.size_by_burst:
        if burst_magnitude <= row.burst_lte:
            return row
    return aff.size_by_burst[-1]


def roll_race(
    bundle: CookbookBundle,
    look: str,
    rng: random.Random,
    *,
    exclude: list[str] | None = None,
) -> RaceDef | None:
    """Affinity-weighted RACE roll (spec §4.2: bias, never lock).

    Any RACE with weight > 0 under this LOOK can be selected. `exclude`
    drops RACE ids from the pool (used by the assembler's observable
    low-ceiling re-roll); returns None when the pool is exhausted. A
    LOOK absent from look_race_affinity is a content bug — fail loud
    (§7).
    """
    weights = bundle.affinities.look_race_affinity.get(look)
    if not weights:
        raise KeyError(
            f"cookbook: LOOK '{look}' absent from look_race_affinity (spec §7 — no silent fallback)"
        )
    by_id = {r.id: r for r in bundle.races}
    missing = [rid for rid in weights if rid not in by_id]
    if missing:
        raise KeyError(f"cookbook: affinity references unknown RACE(s) {missing} for LOOK '{look}'")
    drop = set(exclude or ())
    candidates = [(by_id[rid], w) for rid, w in weights.items() if w > 0 and rid not in drop]
    if not candidates:
        return None
    population = [r for r, _ in candidates]
    weight_vals = [w for _, w in candidates]
    return rng.choices(population, weights=weight_vals, k=1)[0]


def _telegraph(race: RaceDef, band_id: str) -> str:
    return race.telegraph.get(band_id, "")


def build_wandering_table(bundle: CookbookBundle, race: RaceDef, band: CrBand) -> list[dict]:
    """curated ∩ race.filter ∩ cr_band, weighted, per-row telegraph.

    Re-keys the shipped encounter_tables.yaml row shape (weight, count,
    description→telegraph) from regions→levels to race × cr_band, with
    the keeper-awareness scaffolding stripped (spec §6). count uses the
    same dice-string convention as the source pattern; oq-1's
    materializer rolls it (and does CR→Edge there).
    """
    curated = apply_world_register(bundle.monsters, bundle.register)
    rows = resolve_race(curated, race, cr_min=band.cr_min, cr_max=band.cr_max)
    out: list[dict] = []
    for mon in rows:
        # Rarer = scarcer: weight falls off with CR within the band.
        weight = max(1, int(round((band.cr_max - mon.cr) + 1)))
        out.append(
            {
                "name": mon.name,
                "cr": mon.cr,
                "xp": mon.xp,
                "type": mon.type,
                "weight": weight,
                "count": "1" if mon.cr >= 5 else "1d4",
                "telegraph": _telegraph(race, band.id),
            }
        )
    return out


def build_loot_table(
    bundle: CookbookBundle,
    race: RaceDef,
    band: CrBand,
    *,
    rolls: int,
    rng: random.Random,
) -> list[dict]:
    """items ∩ rarity_by_band[band], race.loot_bias applied (multipliers).

    Mirrors the wiring-tested equipment_tables roll-on-list pattern: a
    slot of candidate ids, ids must resolve (they are corpus rows by
    construction here). loot_bias nudges category weight (spec §4.2).
    """
    rarity_weights = bundle.affinities.rarity_by_band.get(band.id, {})
    pool = [it for it in bundle.items if it.rarity in rarity_weights]
    if not pool:
        raise RuntimeError(
            f"cookbook: loot pool empty for band '{band.id}' "
            f"(rarities {list(rarity_weights)}) — spec §7 loud failure"
        )
    cat_bias = race.loot_bias.category_weight
    weights = [rarity_weights[it.rarity] * cat_bias.get(it.item_type, 1.0) for it in pool]
    picks = rng.choices(pool, weights=weights, k=rolls)
    return [{"name": p.name, "item_type": p.item_type, "rarity": p.rarity} for p in picks]
