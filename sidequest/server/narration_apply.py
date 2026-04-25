"""Apply NarrationTurnResult mutations to GameSnapshot.

Extracted from session_handler.py — pure functions over snapshot + result.
Re-exported by session_handler for back-compat.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.game.session import GameSnapshot, NpcRegistryEntry
from sidequest.genre.models.pack import GenrePack
from sidequest.server.session_helpers import (
    _detect_npc_identity_drift,
    _find_confrontation_def,
)
from sidequest.telemetry.spans import SPAN_NPC_AUTO_REGISTERED
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

if TYPE_CHECKING:
    from sidequest.game.encounter import StructuredEncounter

logger = logging.getLogger(__name__)


def _apply_narration_result_to_snapshot(
    snapshot: GameSnapshot,
    result: object,
    player_name: str,
    *,
    pack: GenrePack | None = None,
    dice_failed: bool | None = None,
) -> None:
    """Apply narrator-extracted fields to the snapshot.

    Phase 1: location, quest_updates, lore_established, npc_registry,
    inventory items_gained / items_lost.
    Story 3.4: encounter instantiation and beat application (when pack provided).

    ``dice_failed=True`` + structured ``failure_metric_delta`` substitutes
    the failure value for the beat's default ``metric_delta``. ``None``
    = no dice this turn → default delta. ``False`` = success → default delta.
    """
    from sidequest.agents.orchestrator import NarrationTurnResult

    if not isinstance(result, NarrationTurnResult):
        return

    if result.location:
        old_loc = snapshot.location
        snapshot.location = result.location
        if result.location not in snapshot.discovered_regions:
            snapshot.discovered_regions.append(result.location)
        logger.info(
            "state.location_update old=%r new=%r player=%s",
            old_loc,
            result.location,
            player_name,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "location",
                "before": old_loc,
                "after": result.location,
                "player_name": player_name,
                "turn_number": snapshot.turn_manager.interaction,
                "discovered_count": len(snapshot.discovered_regions),
            },
            component="state.location",
        )

    if result.quest_updates:
        for quest_id, status in result.quest_updates.items():
            snapshot.quest_log[quest_id] = status
        logger.info(
            "state.quest_update count=%d player=%s",
            len(result.quest_updates),
            player_name,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "quest_log",
                "updates": dict(result.quest_updates),
                "player_name": player_name,
                "turn_number": snapshot.turn_manager.interaction,
            },
            component="quest_log",
        )

    # Inventory — apply narrator items_gained/items_lost on the rolling
    # player's character. Playtest 2026-04-24 found a wiring gap: watcher
    # emitted but inventory.items never updated, leaving UI out of sync.
    # Item shape mirrors dispatch/chargen_loadout._item_dict_from_catalog.
    # items_lost removes the first matching name (case-insensitive) —
    # narrator-granted items currently arrive as quantity=1 singletons.
    if (result.items_gained or result.items_lost) and snapshot.characters:
        character = snapshot.characters[0]
        turn_num = snapshot.turn_manager.interaction

        def _narrator_item_dict(entry: dict[str, object]) -> dict[str, object]:
            name_val = str(entry.get("name", "") or "").strip() or "Unknown Item"
            desc_val = str(entry.get("description", "") or "").strip() or (
                "An item acquired during adventure."
            )
            category_raw = str(entry.get("category", "") or "").strip().lower()
            allowed = {
                "weapon", "armor", "tool", "consumable", "quest", "treasure", "misc",
            }
            category = category_raw if category_raw in allowed else "misc"
            slug = name_val.lower().replace(" ", "_").replace("-", "_")
            return {
                "id": f"narrator:{slug}",
                "name": name_val,
                "description": desc_val,
                "category": category,
                "value": 0,
                "weight": 0.0,
                "rarity": "common",
                "narrative_weight": 0.5,
                "tags": [],
                "equipped": False,
                "quantity": 1,
                "uses_remaining": None,
                "state": "Carried",
            }

        added_names: list[str] = []
        for entry in result.items_gained or []:
            item_dict = _narrator_item_dict(entry)
            character.core.inventory.items.append(item_dict)
            added_names.append(str(item_dict["name"]))

        removed_names: list[str] = []
        for entry in result.items_lost or []:
            lost_name = str(entry.get("name", "") or "").strip().lower()
            if not lost_name:
                continue
            for idx, existing in enumerate(character.core.inventory.items):
                existing_name = str(existing.get("name", "") or "").strip().lower()
                if existing_name == lost_name:
                    character.core.inventory.items.pop(idx)
                    removed_names.append(lost_name)
                    break

        logger.info(
            "state.inventory_update player=%s turn=%d gained=%s lost=%s",
            player_name,
            turn_num,
            added_names,
            removed_names,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "inventory",
                "op": "narrator_extracted",
                "gained": added_names,
                "lost": removed_names,
                "player_name": player_name,
                "turn_number": turn_num,
            },
            component="inventory",
        )

    if result.lore_established:
        for lore in result.lore_established:
            if lore not in snapshot.lore_established:
                snapshot.lore_established.append(lore)

    # NPC registry — auto-register + drift detection (Story 37-44).
    turn_num = snapshot.turn_manager.interaction
    for npc_mention in result.npcs_present:
        existing = next(
            (e for e in snapshot.npc_registry if e.name.lower() == npc_mention.name.lower()),
            None,
        )
        if existing is None:
            snapshot.npc_registry.append(
                NpcRegistryEntry(
                    name=npc_mention.name,
                    role=npc_mention.role or None,
                    pronouns=npc_mention.pronouns or None,
                    appearance=npc_mention.appearance or None,
                    last_seen_location=snapshot.location or None,
                    last_seen_turn=turn_num,
                )
            )
            logger.info(
                "%s name=%r pronouns=%r role=%r turn=%d",
                SPAN_NPC_AUTO_REGISTERED,
                npc_mention.name,
                npc_mention.pronouns or "",
                npc_mention.role or "",
                turn_num,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "npc_registry",
                    "op": "auto_registered",
                    "name": npc_mention.name,
                    "pronouns": npc_mention.pronouns or "",
                    "role": npc_mention.role or "",
                    "turn_number": turn_num,
                    "registry_len": len(snapshot.npc_registry),
                },
                component="npc_registry",
            )
        else:
            _detect_npc_identity_drift(existing, npc_mention, turn_num)
            existing.last_seen_turn = turn_num
            existing.last_seen_location = snapshot.location or None
            # Additive-only upsert: never overwrite a canonical field once set.
            # Without this, drift logs once then silently canonicalizes.
            if npc_mention.role and not existing.role:
                existing.role = npc_mention.role
            if npc_mention.pronouns and not existing.pronouns:
                existing.pronouns = npc_mention.pronouns
            if npc_mention.appearance and not existing.appearance:
                existing.appearance = npc_mention.appearance

    # Encounter lifecycle (Story 3.4)
    if pack is not None:
        from sidequest.game.encounter import EncounterPhase, MetricDirection
        from sidequest.server.dispatch.confrontation import find_confrontation_def
        from sidequest.server.dispatch.encounter_lifecycle import (
            instantiate_encounter_from_trigger,
        )
        from sidequest.telemetry.spans import (
            combat_tick_span,
            encounter_beat_applied_span,
            encounter_beat_failure_branch_span,
            encounter_empty_actor_list_span,
            encounter_phase_transition_span,
            encounter_resolved_span,
        )

        # (a) Narrator-initiated encounter
        if result.confrontation and (
            snapshot.encounter is None or snapshot.encounter.resolved
        ):
            # Lie-detector: confrontation without npcs_present means the
            # combatant list will be [player_name] only. Proper fix is in
            # the extraction prompt — this span surfaces the contract break.
            if not result.npcs_present:
                with encounter_empty_actor_list_span(
                    encounter_type=result.confrontation,
                    genre_slug=snapshot.genre_slug or "",
                    player_name=player_name,
                ):
                    logger.warning(
                        "encounter.empty_actor_list confrontation=%s player=%s — "
                        "narrator emitted confrontation without npcs_present; "
                        "panel will render with player only",
                        result.confrontation,
                        player_name,
                    )
            combatants = [e.name for e in result.npcs_present] or [player_name]
            combatants = [player_name] + [c for c in combatants if c != player_name]
            instantiate_encounter_from_trigger(
                snapshot=snapshot,
                pack=pack,
                encounter_type=result.confrontation,
                combatants=combatants,
                hp=10,
                genre_slug=snapshot.genre_slug,
            )

        # (b) Apply beat_selections
        enc = snapshot.encounter
        if enc is not None and not enc.resolved and result.beat_selections:
            cdef = find_confrontation_def(
                pack.rules.confrontations if pack.rules else [],
                enc.encounter_type,
            )
            if cdef is None:
                raise ValueError(
                    f"active encounter type {enc.encounter_type!r} not in pack"
                )
            beat_by_id = {b.id: b for b in cdef.beats}
            prev_phase = enc.structured_phase
            # SOUL Agency: on a dice-replay turn the player's beat was
            # already applied in dispatch/dice.py. Filtering ALL narrator
            # beat_selections here keeps the dice roll as the single
            # mechanical event of the turn — the narrator can describe
            # NPC responses without invisible-mechanics side effects.
            selections = result.beat_selections
            if dice_failed is not None and selections:
                dropped = [(s.actor, s.beat_id) for s in selections]
                logger.info(
                    "encounter.agent_beat_selection_filtered "
                    "reason=dice_replay_turn player=%s dropped=%s",
                    player_name,
                    dropped,
                )
                selections = []
            for sel in selections:
                beat = beat_by_id.get(sel.beat_id)
                if beat is None:
                    raise ValueError(
                        f"unknown beat_id {sel.beat_id!r} for encounter "
                        f"{enc.encounter_type!r}"
                    )
                # ADR-074: dice-failure with structured failure_metric_delta
                # substitutes the failure value (matches narrator tooltip risk).
                applied_delta = beat.metric_delta
                took_failure_branch = (
                    dice_failed is True
                    and beat.failure_metric_delta is not None
                )
                if took_failure_branch:
                    applied_delta = beat.failure_metric_delta
                    with encounter_beat_failure_branch_span(
                        encounter_type=enc.encounter_type,
                        beat_id=sel.beat_id,
                        actor=sel.actor,
                        base_delta=beat.metric_delta,
                        failure_delta=beat.failure_metric_delta,
                    ):
                        logger.info(
                            "encounter.beat_failure_branch beat=%s actor=%s "
                            "base=%d failure=%d effect=%r",
                            sel.beat_id,
                            sel.actor,
                            beat.metric_delta,
                            beat.failure_metric_delta,
                            beat.failure_effect,
                        )
                with encounter_beat_applied_span(
                    encounter_type=enc.encounter_type,
                    actor=sel.actor,
                    beat_id=sel.beat_id,
                    metric_delta=applied_delta,
                ):
                    enc.metric.current += applied_delta
                    if (
                        enc.metric.direction == MetricDirection.Ascending
                        and enc.metric.current < 0
                    ):
                        enc.metric.current = 0
                enc.beat += 1
                _advance_phase(enc)
                with combat_tick_span(
                    encounter_type=enc.encounter_type,
                    beat=enc.beat,
                    phase=(enc.structured_phase or EncounterPhase.Setup).value,
                ):
                    pass
                # Direction-aware threshold check — Ascending fires on high
                # only, Descending on low only, Bidirectional on either.
                m = enc.metric
                if m.direction == MetricDirection.Ascending:
                    threshold_hit = (
                        m.threshold_high is not None and m.current >= m.threshold_high
                    )
                elif m.direction == MetricDirection.Descending:
                    threshold_hit = (
                        m.threshold_low is not None and m.current <= m.threshold_low
                    )
                else:  # Bidirectional
                    threshold_hit = (
                        (m.threshold_high is not None and m.current >= m.threshold_high)
                        or (m.threshold_low is not None and m.current <= m.threshold_low)
                    )
                if threshold_hit or beat.resolution:
                    enc.resolved = True
                    enc.structured_phase = EncounterPhase.Resolution
                    enc.outcome = f"resolved at beat {enc.beat}"
                    with encounter_resolved_span(
                        encounter_type=enc.encounter_type,
                        outcome=enc.outcome,
                        source="metric",
                    ):
                        pass
                    break
            if prev_phase != enc.structured_phase:
                with encounter_phase_transition_span(
                    from_phase=(prev_phase.value if prev_phase else "None"),
                    to_phase=(enc.structured_phase.value
                              if enc.structured_phase else "None"),
                    encounter_type=enc.encounter_type,
                ):
                    pass


def _advance_phase(enc: StructuredEncounter) -> None:
    """Promote encounter phase by beat count (Rust encounter.rs ladder)."""
    from sidequest.game.encounter import EncounterPhase
    if enc.structured_phase is None:
        enc.structured_phase = EncounterPhase.Setup
    ladder = {
        0: EncounterPhase.Setup,
        1: EncounterPhase.Opening,
        2: EncounterPhase.Escalation,
        3: EncounterPhase.Escalation,
        4: EncounterPhase.Escalation,
    }
    enc.structured_phase = ladder.get(enc.beat, EncounterPhase.Climax)


def apply_encounter_updates(
    snapshot: GameSnapshot,
    result: object,
    genre_pack: GenrePack,
    player_name: str,
) -> None:
    """Materialize, advance, and resolve encounter state from narrator output.

    Three cases:
    1. No encounter + confrontation hint: instantiate StructuredEncounter
       from the matching ConfrontationDef.
    2. Active encounter + beat_selections: apply each beat's metric_delta;
       resolution beat or threshold crossing → resolved.
    3. Active encounter + new confrontation hint: no-op (trust existing state).

    Each step emits a state_transition watcher event for the GM panel.
    """
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.game.encounter import (
        EncounterActor,
        EncounterMetric,
        EncounterPhase,
        MetricDirection,
        StructuredEncounter,
    )

    if not isinstance(result, NarrationTurnResult):
        return

    confrontation_hint = result.confrontation
    turn_num = snapshot.turn_manager.interaction

    if snapshot.encounter is None and confrontation_hint:
        conf_def = _find_confrontation_def(genre_pack, confrontation_hint)
        if conf_def is None:
            logger.warning(
                "encounter.skipped reason=no_matching_def type=%s player=%s",
                confrontation_hint,
                player_name,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "skipped",
                    "reason": "no_matching_def",
                    "confrontation_type": confrontation_hint,
                },
                component="encounter",
                severity="warning",
            )
            return
        # Hostile-role detection: substring match on "combat", "hostile",
        # "enemy", "combatant", or named roles like "bandit"/"creature".
        # Tactical grid is Phase 4.
        actors: list[EncounterActor] = []
        if snapshot.characters:
            player_actor_name = snapshot.characters[0].core.name or player_name
        else:
            player_actor_name = player_name
        actors.append(
            EncounterActor(name=player_actor_name, role="player", per_actor_state={})
        )
        hostile_keywords = {"combat", "hostile", "enemy", "combatant"}
        for npc in result.npcs_present or []:
            role = (npc.role or "").lower()
            if any(k in role for k in hostile_keywords) or role in {"brood-mother", "predator"}:
                actors.append(
                    EncounterActor(name=npc.name, role="combatant", per_actor_state={})
                )
        md = conf_def.metric
        direction_map = {
            "ascending": MetricDirection.Ascending,
            "descending": MetricDirection.Descending,
            "bidirectional": MetricDirection.Bidirectional,
        }
        metric = EncounterMetric(
            name=md.name,
            current=md.starting,
            starting=md.starting,
            direction=direction_map.get(md.direction, MetricDirection.Bidirectional),
            threshold_high=md.threshold_high,
            threshold_low=md.threshold_low,
        )
        snapshot.encounter = StructuredEncounter(
            encounter_type=conf_def.confrontation_type,
            metric=metric,
            beat=0,
            structured_phase=EncounterPhase.Setup,
            actors=actors,
            outcome=None,
            resolved=False,
            mood_override=conf_def.mood,
            narrator_hints=[],
        )
        logger.info(
            "encounter.started type=%s metric=%s=%d actors=%d player=%s",
            conf_def.confrontation_type,
            metric.name,
            metric.current,
            len(actors),
            player_name,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "started",
                "confrontation_type": conf_def.confrontation_type,
                "metric_name": metric.name,
                "metric_current": metric.current,
                "actors": [a.name for a in actors],
                "turn_number": turn_num,
            },
            component="encounter",
        )

    if snapshot.encounter is not None and result.beat_selections:
        conf_def = _find_confrontation_def(
            genre_pack, snapshot.encounter.encounter_type
        )
        if conf_def is None:
            return
        beat_lookup = {b.id: b for b in conf_def.beats}
        resolved_this_turn = False
        for selection in result.beat_selections:
            beat_id = getattr(selection, "beat_id", None) or ""
            actor = getattr(selection, "actor", None) or ""
            beat_def = beat_lookup.get(beat_id)
            if beat_def is None:
                logger.warning(
                    "encounter.beat_skipped reason=unknown_beat_id beat_id=%r actor=%r",
                    beat_id,
                    actor,
                )
                _watcher_publish(
                    "state_transition",
                    {
                        "field": "encounter",
                        "op": "beat_skipped",
                        "reason": "unknown_beat_id",
                        "beat_id": beat_id,
                        "actor": actor,
                    },
                    component="encounter",
                    severity="warning",
                )
                continue
            before = snapshot.encounter.metric.current
            snapshot.encounter.metric.current += int(beat_def.metric_delta or 0)
            snapshot.encounter.beat += 1
            logger.info(
                "encounter.beat_applied beat=%s actor=%s metric=%s %d->%d",
                beat_id,
                actor,
                snapshot.encounter.metric.name,
                before,
                snapshot.encounter.metric.current,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "beat_applied",
                    "beat_id": beat_id,
                    "actor": actor,
                    "metric_before": before,
                    "metric_after": snapshot.encounter.metric.current,
                    "metric_delta": beat_def.metric_delta,
                    "turn_number": turn_num,
                },
                component="encounter",
            )
            if beat_def.resolution:
                resolved_this_turn = True
                snapshot.encounter.outcome = beat_def.id
        metric = snapshot.encounter.metric
        hit_high = (
            metric.threshold_high is not None
            and metric.current >= metric.threshold_high
        )
        hit_low = (
            metric.threshold_low is not None
            and metric.current <= metric.threshold_low
        )
        if resolved_this_turn or hit_high or hit_low:
            etype = snapshot.encounter.encounter_type
            outcome = (
                snapshot.encounter.outcome
                or ("threshold_high" if hit_high else "threshold_low" if hit_low else "resolved")
            )
            logger.info(
                "encounter.resolved type=%s outcome=%s final_metric=%d",
                etype,
                outcome,
                metric.current,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "resolved",
                    "confrontation_type": etype,
                    "outcome": outcome,
                    "final_metric": metric.current,
                    "turn_number": turn_num,
                },
                component="encounter",
            )
            snapshot.encounter = None
