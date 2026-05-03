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
    npc_registry_hp_set_span,
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
    raise ValueError(f"actor {actor_name!r} declared_side={declared!r} not in {_VALID_SIDES}")


def _publish_combat_stats_to_registry(
    *,
    snapshot: GameSnapshot,
    actors: list[EncounterActor],
    opponent_metric,
    turn: int,
    source: str,
) -> None:
    """Story 45-21: write HP/max_hp from a combat encounter into npc_registry.

    For each opponent-side ``EncounterActor`` whose ``name`` matches an entry
    in ``snapshot.npc_registry``, populate the entry's ``hp`` / ``max_hp``
    using the opponent dial as the canonical pool size:

        max_hp = opponent_metric.threshold
        hp     = max(0, threshold - current)

    The opponent dial is ascending — when ``current`` reaches ``threshold``
    the opponent loses (= dead). Inverting it into a descending HP view
    gives HP-check subsystems a consistent "0 means dead, >0 means alive"
    contract while keeping the dial as the single source of truth.

    Emits one ``npc_registry.hp_set`` OTEL span per write so the GM panel
    can verify the seam fired (CLAUDE.md: every backend fix must add OTEL
    so we can tell whether the subsystem engaged or Claude is improvising).

    No-op for actors with no matching registry entry — the auto-register
    seam in ``narration_apply`` adds entries; here we only update what is
    already there. CLAUDE.md "no silent fallback": the no-match case is
    expected for the player actor and registry-fallback synthetic mentions
    that haven't been auto-registered yet.
    """
    if opponent_metric is None:
        return
    threshold = int(getattr(opponent_metric, "threshold", 0) or 0)
    current = int(getattr(opponent_metric, "current", 0) or 0)
    if threshold <= 0:
        # Defensive: a zero-threshold dial would publish hp=0/max_hp=0,
        # which is exactly the bug shape this story exists to fix.
        return
    max_hp = threshold
    hp = max(0, threshold - current)

    by_name = {entry.name: entry for entry in snapshot.npc_registry}
    for actor in actors:
        if actor.side != "opponent":
            continue
        entry = by_name.get(actor.name)
        if entry is None:
            continue
        entry.hp = hp
        entry.max_hp = max_hp
        with npc_registry_hp_set_span(
            npc_name=actor.name,
            hp=hp,
            max_hp=max_hp,
            source=source,
            turn_number=turn,
        ):
            pass


def _registry_fallback_npcs(
    snapshot: GameSnapshot,
    *,
    is_combat: bool,
) -> list:
    """Synthesise NpcMention entries from snapshot.npc_registry.

    Story 45-18 (Playtest 3 Orin): when the narrator emits
    ``confrontation=combat`` with an empty ``npcs_present`` list (the
    structured-output extraction dropped the adversary), the encounter would
    start with ``actors=[player only]`` and opponent-side beats would either
    raise "unknown actor" or be silently dropped — opponent_metric stuck at 0
    for 6 rounds. The registry already records who is in-scene at the
    player's current location from prior turns, so we use it as a fallback
    population source.

    Filter: same location as the player. NPCs last seen elsewhere are NOT
    pulled into the encounter — that would over-register characters who
    happened to be in the registry at all.

    Side: combat encounters default to ``opponent`` for the registry-derived
    NPCs (the per-side dials require this so the opposing-side dial can
    advance when the NPC's beat fires). Non-combat encounters use
    ``neutral`` — the narrator can re-classify them on a later turn via an
    explicit ``npcs_present`` mention.
    """
    from sidequest.agents.orchestrator import NpcMention

    location = snapshot.location
    if not location:
        return []
    default_side = "opponent" if is_combat else "neutral"
    fallback: list = []
    for entry in snapshot.npc_registry:
        if entry.last_seen_location != location:
            continue
        fallback.append(
            NpcMention(
                name=entry.name,
                pronouns=entry.pronouns or "",
                role=entry.role or "",
                appearance=entry.appearance or "",
                side=default_side,
            )
        )
    return fallback


def instantiate_encounter_from_trigger(
    *,
    snapshot: GameSnapshot,
    pack: GenrePack,
    encounter_type: str,
    player_name: str,
    npcs_present: list,
    genre_slug: str | None,
    additional_player_names: list[str] | None = None,
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

    Multiplayer (playtest 2026-05-03 [BUG] — confrontation widget missing
    in-fiction principal): a bundled MP turn produces ONE narrator call with
    both PCs' actions concatenated, but the trigger only carries one
    ``player_name`` — the action submitter for the barrier-firing frame. The
    other PCs in the bundle never reached the actor roster, so the client
    widget rendered only one PC even though both played the round. Pass
    ``additional_player_names`` (typically every other PC in
    ``snapshot.player_seats.values()`` minus ``player_name``) to seat them as
    side="player" actors. Solo callers and tests can leave it as ``None`` for
    back-compat. Sealed-letter (commit-reveal duel) encounters keep the
    strict 1-PC red / 1-NPC blue pairing — the resolver looks up actors by
    role tag and a third PC there would break role lookup.

    When ``npcs_present`` is empty the constructor falls back to NPCs from
    ``snapshot.npc_registry`` whose ``last_seen_location`` matches the
    player's current location (Story 45-18). The registry fallback is only
    consulted when the explicit list is empty — an explicit ``npcs_present``
    is always authoritative.

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
        raise ValueError(f"unknown encounter_type {encounter_type!r} — not in pack confrontations")

    # Story 45-18: registry fallback when narrator's npcs_present is empty.
    # Sealed-letter encounters (commit-reveal duels) require exactly one
    # opponent passed explicitly — the registry fallback would leak any
    # bystander NPC at the location into the duel, so only the legacy path
    # uses the fallback. The sealed-letter validator below still raises if
    # npcs_present is wrong.
    if not npcs_present and cdef.resolution_mode != ResolutionMode.sealed_letter_lookup:
        npcs_present = _registry_fallback_npcs(
            snapshot,
            is_combat=cdef.category == "combat",
        )

    with encounter_confrontation_initiated_span(
        encounter_type=encounter_type,
        genre_slug=genre_slug or "",
    ) as _init_span:
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
                    name=opponent_name,
                    role=ROLE_BLUE,
                    side=opponent_side,
                ),
            ]
        else:
            role = "combatant" if cdef.category == "combat" else "participant"
            actors = [
                EncounterActor(name=player_name, role=role, side="player"),
            ]
            seen_pc_names = {player_name}
            for extra in additional_player_names or []:
                if extra and extra not in seen_pc_names:
                    actors.append(EncounterActor(name=extra, role=role, side="player"))
                    seen_pc_names.add(extra)
            for npc in npcs_present:
                npc_name = getattr(npc, "name", None) or str(npc)
                side_raw = getattr(npc, "side", None) or "neutral"
                side = _validate_side(npc_name, side_raw)
                actors.append(EncounterActor(name=npc_name, role=role, side=side))

        # Story 45-18 AC3: GM-panel observability.
        # Decorate the init span with the registered combatants so Keith can
        # verify the actors array was populated end-to-end (and that the
        # registry fallback is firing on Playtest 3 shapes). OTEL string
        # attributes can't carry rich lists, so combatant_names is comma-joined.
        # ``set_attribute`` is a no-op on NoOp / non-recording spans — safe
        # to call unconditionally.
        _init_span.set_attribute("actor_count", len(actors))
        _init_span.set_attribute(
            "combatant_names",
            ",".join(a.name for a in actors),
        )
        # Playtest 2026-05-03 [BUG] — confrontation widget missing in-fiction
        # principal in MP. The GM panel needs to see how many side="player"
        # actors landed (sealed-letter is always 1; non-sealed-letter scales
        # with seated PC count). A regression to "always 1 PC" in MP shows up
        # here without grepping logs.
        _init_span.set_attribute(
            "pc_actor_count",
            sum(1 for a in actors if a.side == "player"),
        )
        _init_span.set_attribute(
            "pc_actor_names",
            ",".join(a.name for a in actors if a.side == "player"),
        )

        pm = cdef.player_metric
        om = cdef.opponent_metric
        enc = StructuredEncounter(
            encounter_type=encounter_type,
            player_metric=EncounterMetric(
                name=pm.name,
                current=pm.starting,
                starting=pm.starting,
                threshold=pm.threshold,
            ),
            opponent_metric=EncounterMetric(
                name=om.name,
                current=om.starting,
                starting=om.starting,
                threshold=om.threshold,
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
                "turn": snapshot.turn_manager.interaction
                if hasattr(snapshot, "turn_manager")
                else 0,
                "genre_slug": genre_slug or "",
            },
            component="encounter",
        )

        # Story 45-21: combat-stats emit → write HP/max_hp into npc_registry.
        # Playtest 3 (Orin save): the Crawling Scavenger sat in the registry
        # with hp=0/max_hp=0, making it appear always-dead to HP-check
        # subsystems. The handshake is the natural seam — by the time we get
        # here we know the actor list AND the dial threshold (= per-side
        # HP pool), so we can publish a real stat block.
        #
        # Per AC2 ("registry entry cannot report HP=0 unless the NPC is
        # actually dead") we ONLY write when the encounter is combat-category
        # and only for opponent-side actors that have a matching registry
        # entry. Non-combat encounters leave hp/max_hp as ``None`` (= no
        # claim) so the validator's dead-NPC check stays correct.
        if cdef.category == "combat":
            _publish_combat_stats_to_registry(
                snapshot=snapshot,
                actors=actors,
                opponent_metric=enc.opponent_metric,
                turn=snapshot.turn_manager.interaction if hasattr(snapshot, "turn_manager") else 0,
                source="encounter_handshake",
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
            result.crossed_thresholds,
            lore_store,
            turn,
        )
        all_crossed.extend(result.crossed_thresholds)
    return all_crossed
