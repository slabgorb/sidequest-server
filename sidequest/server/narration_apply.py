"""Apply NarrationTurnResult mutations to GameSnapshot.

Extracted from session_handler.py — pure functions over snapshot + result.
Re-exported by session_handler for back-compat.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sidequest.game.session import GameSnapshot, NpcRegistryEntry
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.rules import ResolutionMode
from sidequest.server.dispatch.sealed_letter import (
    SealedLetterOutcome,
    resolve_sealed_letter_lookup,
)
from sidequest.server.session_helpers import (
    _detect_npc_identity_drift,
)
from sidequest.telemetry.spans import (
    inventory_narrator_extracted_span,
    lore_established_span,
    npc_auto_registered_span,
    quest_update_span,
)
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

logger = logging.getLogger(__name__)


def _gate_applies_to_encounter(encounter, pack) -> bool:
    """The SOUL gate fires for legacy apply_beat encounters only.

    Sealed-letter dispatch (dogfight) is itself an explicit secret-commit
    UI — both pilots' commits arrive via that flow, not via prose
    extraction. Excluding sealed-letter from the gate avoids breaking the
    dogfight production path while still locking the legacy beat-loop
    against the [S2-BUG] failure mode.
    """
    if encounter is None or pack is None:
        return False
    from sidequest.server.dispatch.confrontation import find_confrontation_def
    cdef = find_confrontation_def(
        pack.rules.confrontations if pack.rules else [],
        encounter.encounter_type,
    )
    if cdef is None:
        # Pack-data inconsistency — let the downstream code raise its own
        # ValueError so the caller sees the real bug. The gate stays off.
        return False
    return cdef.resolution_mode != ResolutionMode.sealed_letter_lookup


def _filter_inferred_pc_beats(
    selections: list,
    encounter,
    *,
    narrating_player: str,
) -> list:
    """SOUL "The Test" gate (Playtest 2026-04-26 [S2-BUG]).

    Drop every beat selection whose actor is on the player side. Those
    selections are extracted from the narrator's prose — they did NOT
    originate from a ``DICE_THROW`` frame on a player's socket, so they
    fail the explicit-consent contract. NPC (opponent / neutral) beats
    are passed through unchanged: NPCs don't have a player-agency
    contract; the narrator legitimately drives them.

    Each rejected PC beat emits a span + watcher event so the GM panel
    can see the gate firing. Without OTEL the gate is invisible — and
    "is this fix actually working?" is unanswerable.

    ``narrating_player`` is the player whose narration produced these
    selections (used to label ``source`` as ``narrator_self`` when the
    rejected actor IS the narrating PC, ``peer_narration`` otherwise).
    """
    from sidequest.telemetry.spans import encounter_beat_skipped_span

    kept: list = []
    for sel in selections:
        actor = encounter.find_actor(sel.actor) if encounter is not None else None
        side = actor.side if actor is not None else "unknown"
        if side != "player":
            kept.append(sel)
            continue
        # PC-side beat from narrator extraction — REJECT.
        source = (
            "narrator_self" if sel.actor == narrating_player else "peer_narration"
        )
        reason = "inferred_pc_beat_no_explicit_action"
        with encounter_beat_skipped_span(
            reason=reason,
            actor=sel.actor,
            actor_side=side,
            beat_id=sel.beat_id,
            source=source,
            narrating_player=narrating_player,
        ):
            pass
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "inferred_pc_beat_rejected",
                "actor": sel.actor,
                "actor_side": side,
                "beat_id": sel.beat_id,
                "source": source,
                "narrating_player": narrating_player,
                "reason": reason,
            },
            component="confrontation",
            severity="warning",
        )
        logger.warning(
            "confrontation.inferred_pc_beat_rejected actor=%s source=%s "
            "narrating_player=%s beat_id=%s reason=%s",
            sel.actor, source, narrating_player, sel.beat_id, reason,
        )
    return kept


@dataclass
class NarrationApplyOutcome:
    """Aggregate result of applying a NarrationTurnResult to a snapshot.

    Carries the per-dispatch-branch outcome objects so callers can read
    them without re-implementing the dispatch logic. Currently only the
    sealed-letter (dogfight) branch surfaces an outcome — extend with
    additional fields as other branches grow structured returns.

    All fields are ``None`` when the corresponding branch did not fire
    this turn (no encounter, wrong resolution_mode, no beat_selections,
    early-return on non-NarrationTurnResult input, etc.). Callers that
    don't care can ignore the return value entirely — it is purely
    additive over the prior ``None`` return.
    """

    sealed_letter: SealedLetterOutcome | None = None


def _apply_narration_result_to_snapshot(
    snapshot: GameSnapshot,
    result: object,
    player_name: str,
    *,
    pack: GenrePack | None = None,
    dice_failed: bool | None = None,
    dice_actor: str | None = None,
    from_explicit_action: bool = False,
) -> NarrationApplyOutcome:
    """Apply narrator-extracted fields to the snapshot.

    Phase 1: location, quest_updates, lore_established, npc_registry,
    inventory items_gained / items_lost.
    Story 3.4: encounter instantiation and beat application (when pack provided).

    ``dice_failed=True`` / ``False`` signals a dice-replay turn — the dice
    is the mechanical event for the rolling player. ``None`` means no dice
    this turn (free-text turn; narrator's beat_selections stand on their
    declared tier).

    ``dice_actor`` is the rolling actor's name (paired with ``dice_failed``).
    On a dice-replay turn, only that actor's beat selection is filtered out —
    ``dispatch_dice_throw`` already applied it. Other actors' selections
    (typically opponent-side NPCs the narrator routes the round-trip through)
    still apply so the opponent dial can advance and combat is two-sided.
    Playtest 2026-04-25 [P0]: prior behavior dropped *all* selections,
    leaving the opponent dial inert and combat structurally unresolvable.

    ``from_explicit_action`` is False on the production session-handler
    path (the only real call site routes narrator-extracted prose). The
    SOUL-gate (Playtest 2026-04-26 [S2-BUG]) drops every PC-side beat
    selection in that mode and emits ``confrontation
    .inferred_pc_beat_rejected`` watcher events — PC mechanical actions
    MUST trace back to an explicit DICE_THROW frame, never to a peer or
    self narration. Test helpers that simulate the dispatch path may set
    ``from_explicit_action=True`` to bypass the gate.
    """
    from sidequest.agents.orchestrator import NarrationTurnResult

    outcome = NarrationApplyOutcome()

    if not isinstance(result, NarrationTurnResult):
        return outcome

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
        # Span emission replaces the prior direct ``_watcher_publish`` —
        # ``WatcherSpanProcessor`` re-emits the same ``state_transition``
        # event via ``SPAN_ROUTES[SPAN_QUEST_UPDATE]``.
        with quest_update_span(
            updates=result.quest_updates,
            player_name=player_name,
            turn_number=snapshot.turn_manager.interaction,
        ):
            for quest_id, status in result.quest_updates.items():
                snapshot.quest_log[quest_id] = status
            logger.info(
                "state.quest_update count=%d player=%s",
                len(result.quest_updates),
                player_name,
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
        removed_names: list[str] = []
        for entry in result.items_gained or []:
            item_dict = _narrator_item_dict(entry)
            character.core.inventory.items.append(item_dict)
            added_names.append(str(item_dict["name"]))

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

        # Span emission replaces the prior direct ``_watcher_publish`` —
        # ``WatcherSpanProcessor`` re-emits the same ``state_transition``
        # event via ``SPAN_ROUTES[SPAN_INVENTORY_NARRATOR_EXTRACTED]``.
        # ``added_names`` / ``removed_names`` reflect the actual mutation
        # outcome (items_lost is case-insensitive and only records
        # successful matches), so the route-extracted payload is identical
        # to what the prior ``_watcher_publish`` call sent.
        with inventory_narrator_extracted_span(
            gained=added_names,
            lost=removed_names,
            player_name=player_name,
            turn_number=turn_num,
        ):
            logger.info(
                "state.inventory_update player=%s turn=%d gained=%s lost=%s",
                player_name,
                turn_num,
                added_names,
                removed_names,
            )

    if result.lore_established:
        added: list[str] = []
        for lore in result.lore_established:
            if lore not in snapshot.lore_established:
                snapshot.lore_established.append(lore)
                added.append(lore)
        # Span emission drives the ``lore_retrieval`` typed event with
        # ``component=lore`` via ``SPAN_ROUTES[SPAN_LORE_ESTABLISHED]``.
        # No prior ``_watcher_publish`` existed for this path — the GM
        # panel's Lore tab was previously dark for narrator-driven
        # additions.
        with lore_established_span(
            items=added,
            added_count=len(added),
            total=len(snapshot.lore_established),
            player_name=player_name,
            turn_number=snapshot.turn_manager.interaction,
        ):
            logger.info(
                "state.lore_established player=%s turn=%d added=%d total=%d",
                player_name,
                snapshot.turn_manager.interaction,
                len(added),
                len(snapshot.lore_established),
            )

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
            # Span emission replaces the prior direct ``_watcher_publish`` —
            # ``WatcherSpanProcessor`` re-emits the same ``state_transition``
            # event via ``SPAN_ROUTES[SPAN_NPC_AUTO_REGISTERED]``.
            with npc_auto_registered_span(
                npc_name=npc_mention.name,
                pronouns=npc_mention.pronouns or "",
                role=npc_mention.role or "",
                turn_number=turn_num,
                registry_len=len(snapshot.npc_registry),
            ):
                logger.info(
                    "npc.auto_registered name=%r pronouns=%r role=%r turn=%d",
                    npc_mention.name,
                    npc_mention.pronouns or "",
                    npc_mention.role or "",
                    turn_num,
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
        # SOUL "The Test" gate — drop PC-side beats inferred from prose.
        # Production callers leave from_explicit_action=False so every
        # narrator-driven turn passes through this filter; explicit
        # DICE_THROW commits arrive via dispatch_dice_throw, which never
        # reaches this branch. See _filter_inferred_pc_beats docstring.
        #
        # Sealed-letter encounters (dogfight) bypass the gate: that
        # confrontation type's UI is itself a private secret-commit form,
        # so the narrator-extracted commits ARE the explicit-consent
        # frame for both pilots. The gate is scoped to legacy apply_beat
        # PC selections — the path that the playtest [S2-BUG] exposed.
        gated_selections = result.beat_selections
        gate_active = (
            enc is not None
            and not from_explicit_action
            and result.beat_selections
            and _gate_applies_to_encounter(enc, pack)
        )
        if gate_active:
            gated_selections = _filter_inferred_pc_beats(
                result.beat_selections,
                enc,
                narrating_player=player_name,
            )

        if enc is not None and not enc.resolved and gated_selections:
            cdef = find_confrontation_def(
                pack.rules.confrontations if pack.rules else [],
                enc.encounter_type,
            )
            if cdef is None:
                raise ValueError(
                    f"active encounter type {enc.encounter_type!r} not in pack"
                )

            # ---- Sealed-letter lookup branch (T5, dogfight port) ----
            # When the confrontation declares ResolutionMode.sealed_letter_lookup
            # we resolve via cross-product cell lookup instead of the legacy
            # apply_beat path. Maneuver IDs and beat IDs share a namespace by
            # content convention (the dogfight beats ARE the maneuvers — see
            # tests/genre/test_dogfight_content_loading.py::
            # test_dogfight_beats_cover_every_consumed_maneuver), so we
            # repurpose ``beat_selections[].beat_id`` as the maneuver commit
            # for that actor. The resolver raises ValueError when commits are
            # missing a role or when a maneuver isn't in maneuvers_consumed.
            #
            # Sealed-letter resolution is EXCLUSIVE of the legacy beat loop —
            # because maneuver IDs collide with beat IDs by content design,
            # falling through to apply_beat would double-apply mechanics.
            if cdef.resolution_mode == ResolutionMode.sealed_letter_lookup:
                if cdef.interaction_table is None:
                    raise ValueError(
                        f"confrontation {enc.encounter_type!r} declares "
                        f"resolution_mode=sealed_letter_lookup but has no "
                        f"interaction_table — cannot dispatch sealed-letter "
                        f"resolution"
                    )

                commits: dict[str, str] = {}
                for sel in gated_selections:
                    actor = enc.find_actor(sel.actor)
                    if actor is None:
                        raise ValueError(
                            f"beat_selection actor {sel.actor!r} not found "
                            f"on sealed-letter encounter "
                            f"{enc.encounter_type!r}"
                        )
                    commits[actor.role] = sel.beat_id

                sl_outcome = resolve_sealed_letter_lookup(
                    enc, commits, cdef.interaction_table,
                )
                outcome.sealed_letter = sl_outcome
                # Replace, do not append: only the most recent cell's hint
                # is relevant context for the next narrator turn.
                # ``narrator_hints`` is consumed by
                # ``sidequest.agents.encounter_render`` which "; "-joins
                # the list into the prompt — appending across turns would
                # bloat the prompt with stale hints (turn 1's "merge"
                # hint is misleading once turn 5 is a knife fight).
                if sl_outcome.narration_hint:
                    enc.narrator_hints = [sl_outcome.narration_hint]
                else:
                    enc.narrator_hints = []
                # Status-change processing further down still runs because
                # we only short-circuit the beat-selection block, not the
                # whole snapshot mutation phase.
                # Fall-through: skip beat loop by NOT defining beat_by_id
                # and gating the loop below.
                _legacy_beat_path = False
            else:
                _legacy_beat_path = True
                beat_by_id = {b.id: b for b in cdef.beats}
        else:
            _legacy_beat_path = False

        if _legacy_beat_path:
            selections = gated_selections
            if dice_failed is not None and selections:
                # Dice-replay turns: dispatch/dice.py already applied the
                # rolling actor's beat. Drop just THAT actor's selection
                # (would otherwise double-apply); keep every other actor's
                # selection so opponent-side beats actually advance the
                # opponent dial. Playtest 2026-04-25 [P0]: dropping all
                # selections wholesale left opponent_metric stuck at 0
                # forever and combat was structurally one-sided.
                kept: list = []
                for sel in selections:
                    actor = enc.find_actor(sel.actor)
                    side = actor.side if actor else "unknown"
                    is_rolling_actor = (
                        dice_actor is not None and sel.actor == dice_actor
                    )
                    # Fallback when dice_actor wasn't threaded through (older
                    # call sites): drop player-side selections to preserve
                    # the prior no-double-apply guarantee, but no longer
                    # blanket-drop opponent-side selections.
                    if dice_actor is None and side == "player":
                        is_rolling_actor = True
                    if is_rolling_actor:
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
                        continue
                    kept.append(sel)
                selections = kept

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
                # Renamed from `outcome` to `tier` to avoid shadowing the
                # function-scoped `outcome = NarrationApplyOutcome()`. The
                # legacy beat path was silently returning RollOutcome from
                # the last selection instead of the apply-outcome dataclass.
                tier = sel.outcome  # narrator-declared tier
                result_apply = apply_beat(enc, actor, beat, tier, turn=turn_num)
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

    return outcome


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
