"""Encounter lifecycle — instantiation, resolution.

Port of sidequest-api/crates/sidequest-server/src/dispatch/
{state_mutations,tropes,response}.rs combat-sensitive paths (Story 3.4).
"""
from __future__ import annotations

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    MetricDirection,
    StructuredEncounter,
)
from sidequest.game.lore_store import LoreStore
from sidequest.game.resource_pool import ResourceThreshold
from sidequest.game.session import GameSnapshot
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.rules import ConfrontationDef
from sidequest.server.dispatch.confrontation import find_confrontation_def
from sidequest.telemetry.spans import (
    encounter_confrontation_initiated_span,
    encounter_resolved_span,
)


_DIRECTION_BY_NAME: dict[str, MetricDirection] = {
    "ascending": MetricDirection.Ascending,
    "descending": MetricDirection.Descending,
    "bidirectional": MetricDirection.Bidirectional,
}


def _metric_from_cdef(cdef: ConfrontationDef) -> EncounterMetric:
    """Build an EncounterMetric from the pack's declared MetricDef."""
    direction = _DIRECTION_BY_NAME[cdef.metric.direction]
    return EncounterMetric(
        name=cdef.metric.name,
        current=cdef.metric.starting,
        starting=cdef.metric.starting,
        direction=direction,
        threshold_high=cdef.metric.threshold_high,
        threshold_low=cdef.metric.threshold_low,
    )


def instantiate_encounter_from_trigger(
    *,
    snapshot: GameSnapshot,
    pack: GenrePack,
    encounter_type: str,
    combatants: list[str],
    hp: int,
    genre_slug: str,
) -> StructuredEncounter | None:
    """Create a StructuredEncounter when the narrator emits ``confrontation=T``.

    Writes the new encounter to ``snapshot.encounter`` and returns it.
    Returns ``None`` when an active (unresolved) encounter already exists —
    caller leaves the current encounter alone.

    Raises ``ValueError`` when ``encounter_type`` doesn't match any
    ConfrontationDef in the pack (CLAUDE.md: no silent fallback).

    The encounter's metric is taken from the matched ConfrontationDef —
    combat packs declare their own metric (e.g. caverns_and_claudes uses
    "momentum" bidirectional, not generic HP).

    Note: ``GenrePack`` has no ``.slug`` attribute; ``genre_slug`` must be
    passed explicitly by the caller (e.g. from ``sd.genre_slug`` or
    ``snapshot.genre_slug``).
    """
    current = snapshot.encounter
    if current is not None and not current.resolved:
        return None

    defs = pack.rules.confrontations if pack.rules else []
    cdef = find_confrontation_def(defs, encounter_type)
    if cdef is None:
        raise ValueError(
            f"unknown encounter_type {encounter_type!r} — "
            f"not in pack confrontations"
        )

    with encounter_confrontation_initiated_span(
        encounter_type=encounter_type,
        genre_slug=genre_slug,
    ):
        actors = [
            EncounterActor(
                name=n,
                role="combatant" if cdef.category == "combat" else "participant",
                per_actor_state={},
            )
            for n in combatants
        ]
        enc = StructuredEncounter(
            encounter_type=encounter_type,
            metric=_metric_from_cdef(cdef),
            beat=0,
            structured_phase=EncounterPhase.Setup,
            secondary_stats=None,
            actors=actors,
            outcome=None,
            resolved=False,
            mood_override=None,
            narrator_hints=[],
        )
        snapshot.encounter = enc
        return enc


def resolve_encounter_from_trope(
    *,
    snapshot: GameSnapshot,
    trope_id: str,
) -> StructuredEncounter | None:
    """Resolve the active encounter because a trope completed.

    Port of dispatch/tropes.rs:179-181. Returns the resolved encounter
    (for OTEL / payload emission) or ``None`` if nothing to resolve.

    IOU (story 3.4): this helper has no Python caller as of this commit. The
    trope engine has not yet been ported to Python (Phase 3 scope). When the
    trope tick/resolve path lands, hook this function at the completion site
    — match Rust's dispatch/tropes.rs:179-181 pattern. The helper + unit
    tests are here so the future port can just call it.
    """
    enc = snapshot.encounter
    if enc is None or enc.resolved:
        return None
    with encounter_resolved_span(
        encounter_type=enc.encounter_type,
        outcome=f"resolved by trope completion: {trope_id}",
        source="trope",
    ):
        enc.resolve_from_trope(trope_id)
    return enc


def _is_combat_category(pack: GenrePack, encounter_type: str) -> bool:
    """Return True when the ConfrontationDef for ``encounter_type`` declares
    category=='combat'. Port of state_mutations.rs:39 category check."""
    defs = pack.rules.confrontations if pack.rules else []
    for d in defs:
        if d.confrontation_type == encounter_type:
            return d.category == "combat"
    return False


def award_turn_xp(snapshot: GameSnapshot, *, in_combat: bool) -> None:
    """Award per-turn XP to the party lead.

    25 XP when ``in_combat`` is True, 10 otherwise. Port of
    sidequest-api/crates/sidequest-server/src/dispatch/state_mutations.rs:39.
    No-op when the snapshot has no characters.
    """
    if not snapshot.characters:
        return
    delta = 25 if in_combat else 10
    char = snapshot.characters[0]
    char.core.xp = char.core.xp + delta


def apply_resource_patches(
    snapshot: GameSnapshot,
    *,
    affinity_progress: list[tuple[str, int]],
    lore_store: LoreStore,
    turn: int,
) -> list[ResourceThreshold]:
    """Apply each (name, delta) to the named pool; mint threshold lore on crossings.

    Returns the flat list of all ResourceThreshold objects crossed across all
    patches (for OTEL / caller logging). The lore fragments themselves have
    already been added to ``lore_store`` — callers don't need to re-mint.

    Raises ``UnknownResource`` on unknown pool name (CLAUDE.md: no silent
    fallback in the helper). The session-handler caller wraps this call in
    a try/except to keep the narration turn resilient to LLM typos — strict
    helper, lenient caller.
    """
    from sidequest.game.resource_pool import ResourcePatchOp
    from sidequest.game.thresholds import mint_threshold_lore

    all_crossed: list[ResourceThreshold] = []
    for name, delta in affinity_progress:
        op = ResourcePatchOp.Add if delta >= 0 else ResourcePatchOp.Subtract
        value = float(abs(delta))
        result = snapshot.apply_resource_patch_by_name(name, op, value)
        mint_threshold_lore(
            result.crossed_thresholds, lore_store, turn,
        )
        all_crossed.extend(result.crossed_thresholds)
    return all_crossed
