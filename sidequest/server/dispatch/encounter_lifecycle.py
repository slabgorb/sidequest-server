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
