"""Apply NarrationTurnResult mutations to GameSnapshot.

Extracted from session_handler.py — pure functions over snapshot + result.
Re-exported by session_handler for back-compat.
"""
from __future__ import annotations

import logging

from sidequest.game.session import GameSnapshot, NpcRegistryEntry
from sidequest.genre.models.pack import GenrePack
from sidequest.server.session_helpers import (
    _detect_npc_identity_drift,
)
from sidequest.telemetry.spans import SPAN_NPC_AUTO_REGISTERED
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

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

    # Encounter lifecycle (dual-track momentum, spec 2026-04-25)
    if pack is not None:
        from sidequest.game.beat_kinds import apply_beat
        from sidequest.server.dispatch.confrontation import find_confrontation_def
        from sidequest.server.dispatch.encounter_lifecycle import (
            instantiate_encounter_from_trigger,
        )
        from sidequest.telemetry.spans import (
            encounter_beat_skipped_span,
            encounter_empty_actor_list_span,
            encounter_resolved_span,
        )

        # (a) Narrator-initiated encounter
        if result.confrontation and (
            snapshot.encounter is None or snapshot.encounter.resolved
        ):
            if not result.npcs_present:
                with encounter_empty_actor_list_span(
                    encounter_type=result.confrontation,
                    genre_slug=snapshot.genre_slug or "",
                    player_name=player_name,
                ):
                    logger.warning(
                        "encounter.empty_actor_list confrontation=%s player=%s",
                        result.confrontation, player_name,
                    )
            instantiate_encounter_from_trigger(
                snapshot=snapshot,
                pack=pack,
                encounter_type=result.confrontation,
                player_name=player_name,
                npcs_present=result.npcs_present,
                genre_slug=snapshot.genre_slug,
            )

        # (b) Apply beat selections (dice-replay turns short-circuit)
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

            selections = result.beat_selections
            if dice_failed is not None and selections:
                # Dice-replay turns: dispatch/dice.py already applied the beat;
                # narrator beat_selections are dropped.
                for sel in selections:
                    actor = enc.find_actor(sel.actor)
                    side = actor.side if actor else "unknown"
                    with encounter_beat_skipped_span(
                        reason="dice_replay_turn",
                        actor=sel.actor, actor_side=side, beat_id=sel.beat_id,
                    ):
                        pass
                    _watcher_publish(
                        "state_transition",
                        {
                            "field": "encounter",
                            "op": "beat_skipped",
                            "reason": "dice_replay_turn",
                            "actor": sel.actor,
                            "actor_side": side,
                            "beat_id": sel.beat_id,
                        },
                        component="encounter",
                    )
                selections = []

            turn_num = snapshot.turn_manager.interaction
            for sel in selections:
                actor = enc.find_actor(sel.actor)
                if actor is None:
                    raise ValueError(f"unknown actor {sel.actor!r} in beat selection")
                beat = beat_by_id.get(sel.beat_id)
                if beat is None:
                    raise ValueError(
                        f"unknown beat_id {sel.beat_id!r} for encounter {enc.encounter_type!r}"
                    )
                outcome = sel.outcome  # narrator-declared tier
                result_apply = apply_beat(enc, actor, beat, outcome, turn=turn_num)
                if result_apply.skipped_reason is not None:
                    with encounter_beat_skipped_span(
                        reason=result_apply.skipped_reason,
                        actor=actor.name, actor_side=actor.side,
                        beat_id=sel.beat_id,
                    ):
                        pass
                    _watcher_publish(
                        "state_transition",
                        {
                            "field": "encounter",
                            "op": "beat_skipped",
                            "reason": result_apply.skipped_reason,
                            "actor": actor.name,
                            "actor_side": actor.side,
                            "beat_id": sel.beat_id,
                        },
                        component="encounter",
                    )
                    continue
                # Beat was applied successfully — emit ENCOUNTER_BEAT_APPLIED
                own_delta = result_apply.deltas.own if result_apply.deltas else 0
                opp_delta = result_apply.deltas.opponent if result_apply.deltas else 0
                _watcher_publish(
                    "state_transition",
                    {
                        "field": "encounter",
                        "op": "beat_applied",
                        "actor": actor.name,
                        "actor_side": actor.side,
                        "beat_id": sel.beat_id,
                        "beat_kind": str(beat.kind.value) if hasattr(beat.kind, "value") else str(beat.kind),
                        "outcome_tier": sel.outcome.value if hasattr(sel.outcome, "value") else str(sel.outcome),
                        "own_delta": own_delta,
                        "opponent_delta": opp_delta,
                        "metric_target": enc.encounter_type,
                        "turn": turn_num,
                    },
                    component="encounter",
                )
                if result_apply.resolved:
                    with encounter_resolved_span(
                        encounter_type=enc.encounter_type,
                        outcome=enc.outcome or "",
                        source="narrator_beat",
                    ):
                        pass
                    snapshot.pending_resolution_signal = _build_resolution_signal(enc)
                    _watcher_publish(
                        "state_transition",
                        {
                            "field": "encounter",
                            "op": "resolved",
                            "encounter_type": enc.encounter_type,
                            "outcome": enc.outcome or "",
                            "source": "narrator_beat",
                            "final_player_metric": enc.player_metric.current,
                            "final_opponent_metric": enc.opponent_metric.current,
                        },
                        component="encounter",
                    )
                    break

    if result.status_changes:
        from sidequest.game.status import Status, StatusSeverity
        from sidequest.telemetry.spans import encounter_status_added_span
        turn_num = snapshot.turn_manager.interaction
        encounter_type = (
            snapshot.encounter.encounter_type if snapshot.encounter else None
        )
        for entry in result.status_changes:
            actor_name = str(entry.get("actor", "")).strip()
            status_payload = entry.get("status") or {}
            text = str(status_payload.get("text", "")).strip()
            severity_raw = str(status_payload.get("severity", "Scratch"))
            try:
                severity = StatusSeverity(severity_raw)
            except ValueError:
                logger.warning(
                    "status_change.invalid_severity actor=%s severity=%s",
                    actor_name, severity_raw,
                )
                continue
            if not actor_name or not text:
                continue
            target = next(
                (c for c in snapshot.characters if c.core.name == actor_name),
                None,
            )
            if target is None:
                logger.warning(
                    "status_change.unknown_actor actor=%s text=%s",
                    actor_name, text,
                )
                continue
            target.core.statuses.append(Status(
                text=text,
                severity=severity,
                absorbed_shifts=0,
                created_turn=turn_num,
                created_in_encounter=encounter_type,
            ))
            with encounter_status_added_span(
                actor=actor_name, text=text, severity=severity.value,
                source="narrator_extraction",
            ):
                pass
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "status_added",
                    "actor": actor_name,
                    "text": text,
                    "severity": severity.value,
                    "source": "narrator_extraction",
                    "turn": turn_num,
                    "encounter_type": encounter_type,
                },
                component="encounter",
            )


def _build_resolution_signal(enc: object) -> object:
    from sidequest.game.resolution_signal import ResolutionSignal
    return ResolutionSignal(
        encounter_type=enc.encounter_type,
        outcome=enc.outcome or "",
        final_player_metric=enc.player_metric.current,
        final_opponent_metric=enc.opponent_metric.current,
        yielded_actors=tuple(),
        edge_refreshed=0,
    )
