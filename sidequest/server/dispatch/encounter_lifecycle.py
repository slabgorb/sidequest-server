"""Encounter lifecycle — instantiation, resolution.

Port of sidequest-api/crates/sidequest-server/src/dispatch/
{state_mutations,tropes,response}.rs combat-sensitive paths (Story 3.4).
"""
from __future__ import annotations

from sidequest.game.encounter import (
    EncounterActor,
    EncounterPhase,
    StructuredEncounter,
)
from sidequest.game.lore_store import LoreStore
from sidequest.game.resource_pool import ResourceThreshold
from sidequest.game.session import GameSnapshot
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.rules import ResolutionMode
from sidequest.server.dispatch.confrontation import find_confrontation_def
from sidequest.server.dispatch.sealed_letter import ROLE_BLUE, ROLE_RED
from sidequest.telemetry.spans import (
    encounter_confrontation_initiated_span,
    encounter_resolved_span,
)
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

_VALID_SIDES = ("player", "opponent", "neutral")


def _validate_side(actor_name: str, declared: str) -> str:
    """Validate that side is in {player, opponent, neutral}.

    Raises ValueError on invalid value, emitting encounter_invalid_side_span
    for OTEL observability.
    """
    if declared in _VALID_SIDES:
        return declared
    from sidequest.telemetry.spans import encounter_invalid_side_span
    with encounter_invalid_side_span(
        actor_name=actor_name,
        declared_side=declared,
        valid_set="|".join(_VALID_SIDES),
    ):
        pass
    raise ValueError(
        f"actor {actor_name!r} declared_side={declared!r} not in {_VALID_SIDES}"
    )


def instantiate_encounter_from_trigger(
    *,
    snapshot: GameSnapshot,
    pack: GenrePack,
    encounter_type: str,
    player_name: str,
    npcs_present: list,
    genre_slug: str | None,
) -> StructuredEncounter | None:
    """Create a StructuredEncounter when the narrator emits ``confrontation=T``.

    Writes the new encounter to ``snapshot.encounter`` and returns it.
    Returns ``None`` when an active (unresolved) encounter already exists —
    caller leaves the current encounter alone.

    Raises ``ValueError`` when ``encounter_type`` doesn't match any
    ConfrontationDef in the pack (CLAUDE.md: no silent fallback).

    Raises ``ValueError`` when any NPC's side is not in {player, opponent, neutral}
    (CLAUDE.md: no silent fallback). Emits encounter_invalid_side_span for OTEL.

    The encounter's dual dials are taken from the matched ConfrontationDef.
    Actors are assigned side="player" for the calling player and side read from
    each NpcMention's ``side`` field (validated against {player, opponent, neutral}).
    When ``npcs_present`` is empty the encounter is instantiated with the player
    only (lie-detector span is the caller's responsibility).

    Note: ``GenrePack`` has no ``.slug`` attribute; ``genre_slug`` must be
    passed explicitly by the caller (e.g. from ``sd.genre_slug`` or
    ``snapshot.genre_slug``).
    """
    from sidequest.game.encounter import EncounterMetric

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
        genre_slug=genre_slug or "",
    ):
        if cdef.resolution_mode == ResolutionMode.sealed_letter_lookup:
            # Sealed-letter encounters are commit-reveal duels addressed by
            # role tag ("red" / "blue") rather than the generic
            # "combatant" / "participant" labels. The handler at
            # ``server.dispatch.sealed_letter`` looks up actors by role —
            # if these tags drift, the handler raises a "missing role" error
            # that the GM panel can't trace back to the constructor.
            #
            # Validation (CLAUDE.md no-silent-fallbacks):
            #   - the def must carry an interaction_table — without it the
            #     downstream resolver has no cells to look up
            #   - exactly one opponent NPC must be supplied — the player is
            #     red, the opponent is blue, and there is no third role
            if cdef.interaction_table is None:
                raise ValueError(
                    f"confrontation {encounter_type!r} declares "
                    f"resolution_mode=sealed_letter_lookup but has no "
                    f"interaction_table — sealed-letter resolution requires a "
                    f"populated table (loaded via the `_from:` pointer)"
                )
            if len(npcs_present) != 1:
                raise ValueError(
                    f"sealed-letter encounter {encounter_type!r} requires "
                    f"exactly one opponent NPC (player=red, npc=blue); got "
                    f"{len(npcs_present)} npcs_present"
                )
            opponent = npcs_present[0]
            opponent_name = getattr(opponent, "name", None) or str(opponent)
            opponent_side_raw = getattr(opponent, "side", None) or "opponent"
            opponent_side = _validate_side(opponent_name, opponent_side_raw)
            actors = [
                EncounterActor(name=player_name, role=ROLE_RED, side="player"),
                EncounterActor(
                    name=opponent_name, role=ROLE_BLUE, side=opponent_side,
                ),
            ]
        else:
            role = "combatant" if cdef.category == "combat" else "participant"
            actors = [
                EncounterActor(name=player_name, role=role, side="player"),
            ]
            for npc in npcs_present:
                npc_name = getattr(npc, "name", None) or str(npc)
                side_raw = getattr(npc, "side", None) or "neutral"
                side = _validate_side(npc_name, side_raw)
                actors.append(EncounterActor(name=npc_name, role=role, side=side))

        pm = cdef.player_metric
        om = cdef.opponent_metric
        enc = StructuredEncounter(
            encounter_type=encounter_type,
            player_metric=EncounterMetric(
                name=pm.name, current=pm.starting,
                starting=pm.starting, threshold=pm.threshold,
            ),
            opponent_metric=EncounterMetric(
                name=om.name, current=om.starting,
                starting=om.starting, threshold=om.threshold,
            ),
            beat=0,
            structured_phase=EncounterPhase.Setup,
            secondary_stats=None,
            actors=actors,
            outcome=None,
            resolved=False,
            mood_override=cdef.mood,
            narrator_hints=[],
        )
        snapshot.encounter = enc
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "started",
                "encounter_type": encounter_type,
                "player_metric_threshold": pm.threshold,
                "opponent_metric_threshold": om.threshold,
                "turn": snapshot.turn_manager.interaction if hasattr(snapshot, "turn_manager") else 0,
                "genre_slug": genre_slug or "",
            },
            component="encounter",
        )
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
    _watcher_publish(
        "state_transition",
        {
            "field": "encounter",
            "op": "resolved",
            "encounter_type": enc.encounter_type,
            "outcome": enc.outcome or f"resolved by trope completion: {trope_id}",
            "source": "trope",
            "final_player_metric": enc.player_metric.current,
            "final_opponent_metric": enc.opponent_metric.current,
        },
        component="encounter",
    )
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
