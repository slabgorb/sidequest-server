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
from sidequest.game.cookbook.loader import CookbookBundle, CookbookValidationError
from sidequest.game.cookbook.models import (
    Affinities,
    CrBand,
    RaceDef,
    RegionContentManifest,
    SizeBudget,
)
from sidequest.telemetry.spans import cookbook_race_reroll_span


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


def roll_big_bad(
    bundle: CookbookBundle,
    race: RaceDef,
    band: CrBand,
    *,
    is_first_band_entry: bool,
    rng: random.Random,
) -> dict | None:
    """Spec §4.2/§4.3 capstone gate.

    Fires iff (a) this band is in big_bad_gate.on_first_band_entry AND
    is_first_band_entry, OR (b) the band's recurring_chance roll hits.
    The capstone is drawn from race.big_bads whose min_band ≤ this band
    (ordinal per §4.2). Returns None when the gate does not fire or no
    eligible big_bad exists. is_first_band_entry is oq-1-supplied (see
    plan Seam Clarification).
    """
    gate = bundle.affinities.big_bad_gate
    order = bundle.affinities.band_order()
    fires = (is_first_band_entry and band.id in gate.on_first_band_entry) or (
        rng.random() < gate.recurring_chance.get(band.id, 0.0)
    )
    if not fires:
        return None
    here = order[band.id]
    eligible = [bb for bb in race.big_bads if order.get(bb.min_band, 1_000) <= here]
    if not eligible:
        return None
    chosen = rng.choice(eligible)
    return {"name": chosen.name, "min_band": chosen.min_band}


def pick_specials(
    bundle: CookbookBundle,
    band: CrBand,
    *,
    budget: int,
    rng: random.Random,
) -> list[dict]:
    """Up to `budget` special rooms whose min_band ≤ this band (ordinal).

    Spec §4.2: feeds oq-1's set-piece slot; we only describe + gate.
    """
    if budget <= 0:
        return []
    order = bundle.affinities.band_order()
    here = order[band.id]
    eligible = [s for s in bundle.specials if order.get(s.min_band, 1_000) <= here]
    if not eligible:
        return []
    rng.shuffle(eligible)
    return [
        {
            "id": s.id,
            "telegraph": s.telegraph,
            "mechanic": s.mechanic,
            "outcome": s.outcome,
            "min_band": s.min_band,
            "feeds_setpiece_slot": s.feeds_setpiece_slot,
        }
        for s in eligible[:budget]
    ]


def _floor_budget_for_capstone(bundle: CookbookBundle):
    """big_bad_forces_size: a capstone is a lair complex (spec §4.2)."""
    target = bundle.affinities.big_bad_forces_size
    # Map the named tier to the largest size_by_burst row (v1: 'large'
    # == the top burst row). If a future affinities.yaml introduces
    # named tiers, resolve here — fail loud on an unknown name.
    if target != "large":
        raise ValueError(
            f"cookbook: unsupported big_bad_forces_size '{target}' "
            f"(v1 supports 'large' only — spec §11 open tuning item)"
        )
    return bundle.affinities.size_by_burst[-1]


def assemble_region(
    bundle: CookbookBundle,
    *,
    campaign_seed: str,
    expansion_id: str,
    depth_score: float,
    burst_magnitude: int,
    look: str,
    is_first_band_entry: bool,
) -> RegionContentManifest:
    """The deterministic content-manifest contract (spec §4.3).

    Pure function of named inputs. depth_score / burst_magnitude / look /
    is_first_band_entry are oq-1-owned signals passed in (never produced
    here). NO CR→Edge translation — that is oq-1's materializer seam
    (ADR-014/078); the manifest carries cr_band + raw corpus rows.
    """
    rng = region_rng(campaign_seed, expansion_id)
    band = band_for_depth(bundle.affinities, depth_score)
    race = roll_race(bundle, look, rng)
    wandering = build_wandering_table(bundle, race, band)
    # Data-Forced Design Item: a low-ceiling RACE (ooze/goblinoid) may
    # not fill this depth. Yield OBSERVABLY to another affinity RACE —
    # never emit a silent empty table (spec §7). Bounded, deterministic.
    if not wandering:
        excluded: list[str] = [race.id]
        from_race = race.id
        while True:
            nxt = roll_race(bundle, look, rng, exclude=excluded)
            if nxt is None:
                raise CookbookValidationError(
                    f"every affinity RACE for LOOK '{look}' is empty at "
                    f"band '{band.id}' — content bug (validate_bundle "
                    f"should have caught this)"
                )
            cand = build_wandering_table(bundle, nxt, band)
            if cand:
                with cookbook_race_reroll_span(
                    look=look,
                    band=band.id,
                    from_race=from_race,
                    to_race=nxt.id,
                    excluded=excluded,
                ):
                    pass
                race, wandering = nxt, cand
                break
            excluded.append(nxt.id)
    big_bad = roll_big_bad(bundle, race, band, is_first_band_entry=is_first_band_entry, rng=rng)
    budget = (
        _floor_budget_for_capstone(bundle)
        if big_bad is not None
        else budget_for_burst(bundle.affinities, burst_magnitude)
    )
    loot = build_loot_table(bundle, race, band, rolls=budget.loot_rolls, rng=rng)
    specials = pick_specials(bundle, band, budget=budget.special_rooms, rng=rng)
    return RegionContentManifest(
        race=race.id,
        cr_band=band.id,
        size_budget={
            "wandering_rolls": budget.wandering_rolls,
            "special_rooms": budget.special_rooms,
            "loot_rolls": budget.loot_rolls,
        },
        wandering_table=wandering,
        loot_table=loot,
        special_rooms=specials,
        big_bad=big_bad,
    )
