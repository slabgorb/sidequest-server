"""Apply NarrationTurnResult mutations to GameSnapshot.

Extracted from session_handler.py — pure functions over snapshot + result.
Re-exported by session_handler for back-compat.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from sidequest.game.region_validation import (
    canonicalize_region_name,
    validate_region_name,
)
from sidequest.game.session import (
    ContainerState,
    GameSnapshot,
    NpcRegistryEntry,
    RoomState,
)
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.rules import ResolutionMode
from sidequest.magic.models import Flag, MagicWorking
from sidequest.magic.state import ApplyWorkingResult, ThresholdCrossingEvent
from sidequest.magic.validator import validate as magic_validate
from sidequest.server.dispatch.sealed_letter import (
    SealedLetterOutcome,
    resolve_sealed_letter_lookup,
)
from sidequest.server.session_helpers import (
    _detect_npc_identity_drift,
)
from sidequest.telemetry.spans import (
    container_retrieval_blocked_span,
    container_retrieval_recorded_span,
    inventory_narrator_extracted_span,
    lore_established_span,
    magic_working_span,
    npc_auto_registered_span,
    npc_pc_name_skipped_span,
    quest_update_span,
    region_entry_canonicalized_dedup_span,
    region_entry_rejected_span,
    trope_resolution_handshake_span,
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


class MagicWorkingParseError(RuntimeError):
    """Raised when ``game_patch.magic_working`` has invalid shape.

    Surfaces three distinct failure modes:
    - snapshot has ``magic_state=None`` but the narrator emitted a working
      (no silent fallback per CLAUDE.md);
    - the dict fails ``MagicWorking`` pydantic validation;
    - the working names an actor that has no instantiated ledger bars
      (call ``magic_state.add_character`` first).
    """


@dataclass
class StatusChangePromotion:
    """A magic threshold crossing promoted to a ``status_changes`` ADD.

    The pipeline reuses the existing ``Status`` renderer downstream — this
    dataclass is only the intermediate shape between
    ``promote_crossings_to_status_changes`` and the snapshot mutation that
    appends a ``Status`` to the actor's ``core.statuses``. Severity is
    carried as a string keyed against ``StatusSeverity[...]`` (the enum's
    member names: ``Scratch`` / ``Wound`` / ``Scar`` / ``Boon``).
    """

    actor: str
    status_text: str
    severity: Literal["Scratch", "Wound", "Scar", "Boon"]


@dataclass
class MagicApplyResult:
    """Aggregate result of applying a ``magic_working`` patch field.

    Wraps the underlying ``ApplyWorkingResult`` (ledger mutations + threshold
    crossings) with the validator's flag list. Returned by
    ``apply_magic_working`` and attached to ``NarrationApplyOutcome.magic``
    so downstream tasks (3.4 status_changes auto-promotion, 3.5 OTEL span)
    can read the threshold crossings + flag severity without re-running
    validation.
    """

    apply: ApplyWorkingResult
    flags: list[Flag]

    @property
    def crossings(self) -> list[ThresholdCrossingEvent]:
        return self.apply.crossings


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

    ``magic`` carries the magic-working apply result when the narrator
    emitted a ``magic_working`` field on this turn's ``game_patch``;
    ``None`` otherwise. Tasks 3.4/3.5 read ``magic.crossings`` and
    ``magic.flags`` to drive auto-promotion + OTEL. Direct callers that
    don't care can ignore it.
    """

    sealed_letter: SealedLetterOutcome | None = None
    magic: MagicApplyResult | None = None


def apply_magic_working(
    *, snapshot: GameSnapshot, patch_field: dict
) -> MagicApplyResult:
    """Parse a ``game_patch.magic_working`` dict, validate, and apply.

    Returns ``MagicApplyResult`` aggregating the mutated ledger /
    threshold crossings (via ``ApplyWorkingResult``) with the validator's
    flag list. Raises ``MagicWorkingParseError`` on:

    - ``snapshot.magic_state is None`` (world has no magic config loaded
      but narrator emitted a working — fail loud per CLAUDE.md
      no-silent-fallback);
    - ``patch_field`` failing ``MagicWorking`` pydantic validation;
    - ``apply_working`` raising ``KeyError`` (actor has no instantiated
      character bars — caller must run ``add_character`` first).

    The caller (the narration_apply pipeline branch below) is responsible
    for promoting threshold crossings to ``status_changes`` (Task 3.4)
    and emitting the ``magic.working_applied`` OTEL span (Task 3.5).
    This function intentionally does NEITHER — it is the parse + validate
    + apply seam only.
    """
    if snapshot.magic_state is None:
        raise MagicWorkingParseError(
            "magic_working emitted but world has no magic_state loaded"
        )
    try:
        working = MagicWorking.model_validate(patch_field)
    except ValidationError as e:
        raise MagicWorkingParseError(
            f"magic_working schema invalid: {e}"
        ) from e

    flags = magic_validate(working, snapshot.magic_state.config)

    try:
        apply_result = snapshot.magic_state.apply_working(working)
    except KeyError as e:
        raise MagicWorkingParseError(f"unknown actor: {e}") from e

    # Task 3.5: emit ``magic.working`` OTEL span + watcher publish so the
    # GM panel sees every working land. Build the post-apply ledger
    # snapshot from the bars touched by this working — world-scope bars
    # (no character-scope bar for the cost type, e.g. ``vitality`` on a
    # world that doesn't track it on the character) are tolerated via
    # ``KeyError`` skip per architect plan §3.5: not every cost_type is
    # surfaced as a character-scope bar; that's a config truth, not a
    # silent fallback for a missing-data bug.
    from sidequest.magic.state import BarKey

    ledger_after: dict[str, float] = {}
    for cost_type in working.costs:
        try:
            bar = snapshot.magic_state.get_bar(
                BarKey(
                    scope="character", owner_id=working.actor, bar_id=cost_type,
                )
            )
        except KeyError:
            continue
        ledger_after[cost_type] = bar.value

    with magic_working_span(
        plugin=working.plugin,
        mechanism=working.mechanism,
        actor=working.actor,
        domain=working.domain,
        narrator_basis=working.narrator_basis,
        costs_debited=dict(working.costs),
        flags=flags,
        ledger_after=ledger_after,
        flavor=working.flavor,
        consent_state=working.consent_state,
        item_id=working.item_id,
        alignment_with_item_nature=working.alignment_with_item_nature,
    ):
        # Direct watcher publish so OTEL-less paths (unit tests, headless
        # playtest drivers without a TracerProvider) still see the
        # working on the dashboard event feed. Mirrors the ``encounter``
        # / ``inventory`` dual-path pattern elsewhere in this module.
        _watcher_publish(
            "state_transition",
            {
                "field": "magic_state",
                "op": "working",
                "plugin": working.plugin,
                "actor": working.actor,
                "mechanism_engaged": working.mechanism,
                "domain": working.domain,
                "narrator_basis": working.narrator_basis,
                "costs_debited": dict(working.costs),
                "flags": [f.model_dump() for f in flags],
                "ledger_after": ledger_after,
                "flavor": working.flavor or "",
                "consent_state": working.consent_state or "",
                "item_id": working.item_id or "",
                "alignment_with_item_nature": (
                    float(working.alignment_with_item_nature)
                    if working.alignment_with_item_nature is not None
                    else 0.0
                ),
            },
            component="magic",
        )

    return MagicApplyResult(apply=apply_result, flags=flags)


def _append_status_to_actor(
    *,
    target,
    actor: str,
    text: str,
    severity,
    source: str,
    turn_num: int,
    encounter_type: str | None,
) -> None:
    """Append a ``Status`` to ``target.core.statuses`` and emit OTEL.

    Centralizes the side-effect shape shared by the narrator-extracted
    ``status_changes`` path and the magic-threshold-promotion path: both
    build the same ``Status`` record, open ``encounter_status_added_span``,
    and publish the same ``state_transition`` watcher event. The only
    caller-visible difference is ``source`` (``narrator_extraction`` vs.
    ``magic_threshold_promotion``), which keeps Sebastien's mechanical-
    visibility lens able to distinguish "narrator said so" from "bar
    fired auto-promotion".

    Each caller is responsible for resolving ``target`` (the
    ``Character``) and emitting its own unknown-actor warning before
    calling — the warning labels differ between the two paths and a
    server-side test asserts the narrator-path warning text exactly.
    """
    from sidequest.game.status import Status
    from sidequest.telemetry.spans import encounter_status_added_span

    target.core.statuses.append(
        Status(
            text=text,
            severity=severity,
            absorbed_shifts=0,
            created_turn=turn_num,
            created_in_encounter=encounter_type,
        )
    )
    with encounter_status_added_span(
        actor=actor,
        text=text,
        severity=severity.value,
        source=source,
    ):
        pass
    _watcher_publish(
        "state_transition",
        {
            "field": "encounter",
            "op": "status_added",
            "actor": actor,
            "text": text,
            "severity": severity.value,
            "source": source,
            "turn": turn_num,
            "encounter_type": encounter_type,
        },
        component="encounter",
    )


def _apply_magic_status_promotions(
    *,
    snapshot: GameSnapshot,
    magic_result: MagicApplyResult,
    player_name: str,
) -> None:
    """Apply Task 3.4 status promotions to ``snapshot.characters``.

    Mirrors the side-effect shape of the manual ``result.status_changes``
    branch below (``Status`` append + watcher publish) so the GM panel
    sees auto-promoted statuses on the same lane as narrator-extracted
    ones. ``source="magic_threshold_promotion"`` distinguishes them in
    the watcher feed — Sebastien's mechanical-visibility lens demands
    that auto-fired statuses are traceable back to the bar that fired
    them, not blurred into "narrator said so".

    Does not raise: a missing actor character (BarKey owner_id with no
    matching ``core.name``) logs and skips. The MagicState ledger and
    the Character roster are populated from different paths (chargen vs.
    add_character) and a soft mismatch shouldn't crash the apply
    pipeline mid-turn.
    """
    from sidequest.game.status import StatusSeverity

    promotions = promote_crossings_to_status_changes(
        result=magic_result, snapshot=snapshot
    )
    if not promotions:
        return

    turn_num = snapshot.turn_manager.interaction
    encounter_type = (
        snapshot.encounter.encounter_type if snapshot.encounter else None
    )
    for promo in promotions:
        target = next(
            (c for c in snapshot.characters if c.core.name == promo.actor),
            None,
        )
        if target is None:
            logger.warning(
                "magic.status_promotion_unknown_actor actor=%s text=%s "
                "player=%s — bar fired but no matching character.core.name",
                promo.actor, promo.status_text, player_name,
            )
            continue
        _append_status_to_actor(
            target=target,
            actor=promo.actor,
            text=promo.status_text,
            severity=StatusSeverity[promo.severity],
            source="magic_threshold_promotion",
            turn_num=turn_num,
            encounter_type=encounter_type,
        )


def promote_crossings_to_status_changes(
    *, result: MagicApplyResult, snapshot: GameSnapshot
) -> list[StatusChangePromotion]:
    """Convert ``MagicApplyResult.crossings`` into status-change promotions.

    Reads the per-bar ``promote_to_status`` config from the world's
    ``LedgerBarSpec`` — NOT a hardcoded module-level dict (architect §5.3,
    2026-04-29). This keeps status text/severity world-tunable: a different
    innate-using world (e.g. victoria-touched) can map ``sanity`` →
    ``"Slipping"``, ``Scar`` without code change. A bar without
    ``promote_to_status`` is silently skipped — the architect explicitly
    calls this out as the right behavior, not a fallback (not every bar
    surfaces as a Status; world-scope bars never do).
    """
    if snapshot.magic_state is None:
        return []

    promotions: list[StatusChangePromotion] = []
    bars_by_id = {b.id: b for b in snapshot.magic_state.config.ledger_bars}

    for crossing in result.crossings:
        spec = bars_by_id.get(crossing.bar_key.bar_id)
        if spec is None or spec.promote_to_status is None:
            # Architect §5.3: silent skip is correct — not every bar
            # promotes. ``spec is None`` would be a config inconsistency
            # (crossing references a bar id not in the config), but the
            # crossing itself was emitted by ``apply_working`` reading
            # the same config, so this branch is structurally
            # unreachable for in-config bars. Keep it defensive without
            # raising — Task 3.5 will add an OTEL span if it ever fires.
            continue
        promotions.append(
            StatusChangePromotion(
                actor=crossing.bar_key.owner_id,
                status_text=spec.promote_to_status.text,
                severity=spec.promote_to_status.severity,
            )
        )
    return promotions


def _apply_narration_result_to_snapshot(
    snapshot: GameSnapshot,
    result: object,
    player_name: str,
    *,
    pack: GenrePack | None = None,
    dice_failed: bool | None = None,
    dice_actor: str | None = None,
    from_explicit_action: bool = False,
    opposed_player_d20: int | None = None,
    opposed_player_beat_id: str | None = None,
    opposed_player_actor: str | None = None,
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

    # Magic working (Coyote Star iter 3 — Task 3.3). Ordered ahead of
    # the location/quest/inventory/encounter branches so the
    # ``magic.working_applied`` OTEL span Task 3.5 will add timestamps
    # before any downstream snapshot mutation — the GM panel reads
    # magic-resolution as the first event of the turn, paired tightly
    # with the prose that produced it. The parse-error path is
    # *swallowed* below (logged + continue) by design: narration is
    # already in the user's hands, so we never crash the apply pipeline
    # on a malformed working — Task 3.5 will promote that log to a
    # ``magic.parse_error`` span. Threshold-crossing → status_changes
    # auto-promotion (Task 3.4) is wired below in the ``else`` branch;
    # the ``magic.working_applied`` OTEL span itself (Task 3.5) is still
    # pending — see that task for the wire-up.
    magic_working_field = getattr(result, "magic_working", None)
    if magic_working_field is not None:
        try:
            outcome.magic = apply_magic_working(
                snapshot=snapshot, patch_field=magic_working_field
            )
        except MagicWorkingParseError as e:
            # Log + continue — narration is already delivered; the parse
            # error must not crash the apply pipeline. Task 3.5 will
            # promote this to a ``magic.parse_error`` OTEL span so the
            # GM panel sees it. Until then, structured logging keeps the
            # failure auditable.
            logger.warning(
                "magic.parse_error player=%s reason=%s",
                player_name,
                e,
            )
        else:
            # Task 3.4: auto-promote threshold crossings into Status.
            # Reuses the existing Status renderer downstream — no new UI.
            # The world's per-bar ``promote_to_status`` block decides the
            # status text + severity (architect §5.3); bars without that
            # block produce no promotion (silent skip is intended).
            _apply_magic_status_promotions(
                snapshot=snapshot,
                magic_result=outcome.magic,
                player_name=player_name,
            )

    if result.location:
        old_loc = snapshot.location
        snapshot.location = result.location
        # Story 45-16: filter narrator-emitted location before adding
        # to the region graph. Playtest 3 leaked
        # `(aside — narrator brief)` into discovered_regions because
        # this seam appended unconditionally. Reject + emit OTEL so
        # Sebastien's lie-detector sees the filter fire.
        is_valid_region, rejection_reason = validate_region_name(result.location)
        if not is_valid_region:
            with region_entry_rejected_span(
                entry=result.location,
                reason=rejection_reason or "unknown",
                caller_path="narration_apply.location_update",
                player_name=player_name,
            ):
                logger.warning(
                    "region.entry_rejected reason=%s entry=%r player=%s caller=narration_apply.location_update",
                    rejection_reason,
                    result.location,
                    player_name,
                )
        else:
            # Story 45-17: canonical-slug dedup. The narrator emits
            # surface variants for the same room across turns
            # (Felix's Playtest 3: "The Crew Quarters" vs "the crew
            # quarters"); compare slugs, not raw strings.
            new_slug = canonicalize_region_name(result.location)
            existing_match: str | None = None
            for existing in snapshot.discovered_regions:
                if canonicalize_region_name(existing) == new_slug:
                    existing_match = existing
                    break
            if existing_match is None:
                snapshot.discovered_regions.append(result.location)
            elif existing_match != result.location:
                # Surface variants — emit dedup span so the GM panel
                # sees the merge fire (CLAUDE.md OTEL principle).
                with region_entry_canonicalized_dedup_span(
                    entry=result.location,
                    canonical_slug=new_slug,
                    existing_surface_form=existing_match,
                    caller_path="narration_apply.location_update",
                    player_name=player_name,
                ):
                    logger.info(
                        "region.entry_canonicalized_dedup entry=%r existing=%r slug=%s caller=narration_apply.location_update",
                        result.location,
                        existing_match,
                        new_slug,
                    )
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
        # Scratch sweep on scene change. A location change is a scene
        # boundary by every TTRPG convention — the cough you took in the
        # previous room shouldn't pile onto the cough you take in the
        # next one (Playtest 2026-04-26 Bug #1). Wound and Scar persist;
        # only Scratch clears. ``old_loc`` is None at session start —
        # don't sweep on the first location set (no scene to leave).
        if old_loc and old_loc != result.location:
            from sidequest.server.status_clear import clear_scratch_on_scene_end
            clear_scratch_on_scene_end(
                snapshot,
                reason="location_change",
                turn=snapshot.turn_manager.interaction,
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

    # Inventory — apply narrator items_gained/items_lost/items_discarded/
    # items_consumed on the rolling player's character. Playtest 2026-04-24
    # found a wiring gap: watcher emitted but inventory.items never
    # updated, leaving UI out of sync. Item shape mirrors
    # dispatch/chargen_loadout._item_dict_from_catalog. items_lost removes
    # the first matching name (case-insensitive) — narrator-granted items
    # currently arrive as quantity=1 singletons. items_discarded (Story
    # 45-14) flips the first matching item's state from "Carried" to
    # "Discarded" without removing it — narrator-recoverable abandon/drop
    # semantics. items_consumed (Story 45-15) also removes the first
    # matching item but is a distinct lane so the OTEL span can surface
    # "spent on use" vs. "given away" — Playtest 3 Felix found the
    # maintenance kit lingered at quantity=1 after patch-foam use because
    # the consume verb had no apply seam.
    items_discarded = getattr(result, "items_discarded", None) or []
    items_consumed = getattr(result, "items_consumed", None) or []
    if (
        result.items_gained or result.items_lost or items_discarded
        or items_consumed
    ) and snapshot.characters:
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
        discarded_names: list[str] = []
        unmatched_discards: list[str] = []
        consumed_names: list[str] = []
        unmatched_consumes: list[str] = []

        # Story 45-13: per-room container retrieved-state. Each
        # ``items_gained`` entry may carry an optional ``from_container``
        # annotation pointing at a narrator-emitted container id (e.g.
        # ``"tin_box"``). The room id is keyed off ``snapshot.location``
        # — the canonical "where the player is right now" string. The
        # apply-time gate is the load-bearing block per AC #6: even when
        # the prompt-time hint is bypassed, a duplicate retrieval in the
        # same room is filtered here.
        room_id = snapshot.location
        round_number = snapshot.turn_manager.round
        for entry in result.items_gained or []:
            container_id = str(entry.get("from_container", "") or "").strip()
            if container_id and not room_id:
                # No silent fallback (CLAUDE.md): the narrator emitted a
                # container annotation but the snapshot has no canonical
                # room. The gate is unreachable; the item still lands so
                # play does not stall, but the GM panel must see the
                # configuration gap. A future story may want to harden
                # this into a hard refusal once narrator emission is
                # stable.
                logger.warning(
                    "state.container_gate_unreachable player=%s "
                    "container=%s reason=snapshot_location_empty round=%d",
                    player_name, container_id, round_number,
                )
            elif container_id and room_id:
                room_state = snapshot.room_states.get(room_id)
                prior = (
                    room_state.containers.get(container_id)
                    if room_state is not None
                    else None
                )
                if prior is not None and prior.retrieved:
                    # Duplicate retrieval — apply-time gate fires. Item
                    # is NOT appended, prior_retrieved_at_round is
                    # preserved (read-only check, no clobber). The
                    # ContainerState model_validator guarantees that if
                    # ``prior.retrieved`` is True, ``retrieved_at_round``
                    # is a real int; the int(... or 0) cast below is
                    # therefore a defensive belt for None — the validator
                    # is the suspenders.
                    with container_retrieval_blocked_span(
                        room_id=room_id,
                        container_id=container_id,
                        prior_retrieved_at_round=int(
                            prior.retrieved_at_round or 0,
                        ),
                        current_round=round_number,
                        interaction=turn_num,
                        player_name=player_name,
                        genre=snapshot.genre_slug,
                        world=snapshot.world_slug,
                    ):
                        # warning, not info: narrator produced a known-bad
                        # duplicate that the gate had to suppress — this
                        # is a client-side error path per python.md #4.
                        logger.warning(
                            "state.container_retrieval_blocked player=%s "
                            "room=%s container=%s prior_round=%s "
                            "current_round=%s",
                            player_name, room_id, container_id,
                            prior.retrieved_at_round, round_number,
                        )
                    continue  # skip the inventory append for this entry

                # First retrieval — record state and fire recorded span.
                if room_state is None:
                    room_state = RoomState(room_id=room_id)
                    snapshot.room_states[room_id] = room_state
                room_state.containers[container_id] = ContainerState(
                    container_id=container_id,
                    retrieved=True,
                    retrieved_at_round=round_number,
                )
                with container_retrieval_recorded_span(
                    room_id=room_id,
                    container_id=container_id,
                    round_number=round_number,
                    interaction=turn_num,
                    items_gained_count=1,
                    player_name=player_name,
                    genre=snapshot.genre_slug,
                    world=snapshot.world_slug,
                ):
                    logger.info(
                        "state.container_retrieval_recorded player=%s "
                        "room=%s container=%s round=%d",
                        player_name, room_id, container_id, round_number,
                    )

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

        # Story 45-14: items_discarded — transition first matching item's
        # state out of "Carried" instead of removing. Per CLAUDE.md
        # "no silent fallbacks": when the narrator declares a discard for
        # an item that isn't actually in inventory we log the miss and
        # surface it on the OTEL span so the GM panel sees the gap (the
        # narrator hallucinated, or extraction lost the prior pickup).
        for entry in items_discarded:
            discard_name = str(entry.get("name", "") or "").strip().lower()
            if not discard_name:
                continue
            matched = False
            for existing in character.core.inventory.items:
                existing_name = str(existing.get("name", "") or "").strip().lower()
                if existing_name == discard_name and (
                    str(existing.get("state", "Carried")) == "Carried"
                ):
                    existing["state"] = "Discarded"
                    existing["equipped"] = False
                    discarded_names.append(discard_name)
                    matched = True
                    break
            if not matched:
                unmatched_discards.append(discard_name)
                logger.warning(
                    "state.inventory_discard_miss player=%s turn=%d name=%r "
                    "reason=no_carried_match",
                    player_name, turn_num, discard_name,
                )

        # Story 45-15: items_consumed — used-up consumables drop from
        # inventory. AC1 demands no item remain at state=Consumed after
        # end-of-turn; the simplest fix is to never set Consumed in the
        # first place — the consume lane removes outright. Per CLAUDE.md
        # "no silent fallbacks": when the narrator declares a consume for
        # an item that isn't in inventory we surface ``unmatched_consumes``
        # on the OTEL span so the GM panel sees the gap (the narrator
        # hallucinated the use, or extraction lost the prior pickup).
        for entry in items_consumed:
            consume_name = str(entry.get("name", "") or "").strip().lower()
            if not consume_name:
                continue
            matched = False
            for idx, existing in enumerate(character.core.inventory.items):
                existing_name = str(existing.get("name", "") or "").strip().lower()
                if existing_name == consume_name:
                    character.core.inventory.items.pop(idx)
                    consumed_names.append(consume_name)
                    matched = True
                    break
            if not matched:
                unmatched_consumes.append(consume_name)
                logger.warning(
                    "state.inventory_consume_miss player=%s turn=%d "
                    "name=%r reason=no_inventory_match",
                    player_name, turn_num, consume_name,
                )

        # Span emission replaces the prior direct ``_watcher_publish`` —
        # ``WatcherSpanProcessor`` re-emits the same ``state_transition``
        # event via ``SPAN_ROUTES[SPAN_INVENTORY_NARRATOR_EXTRACTED]``.
        # ``added_names`` / ``removed_names`` / ``discarded_names`` /
        # ``consumed_names`` reflect the actual mutation outcome
        # (case-insensitive match, only successful transitions/removals
        # recorded), so the route-extracted payload
        # mirrors the post-mutation state.
        with inventory_narrator_extracted_span(
            gained=added_names,
            lost=removed_names,
            discarded=discarded_names,
            consumed=consumed_names,
            player_name=player_name,
            turn_number=turn_num,
            unmatched_discards_count=len(unmatched_discards),
            unmatched_consumes_count=len(unmatched_consumes),
        ):
            logger.info(
                "state.inventory_update player=%s turn=%d gained=%s lost=%s "
                "discarded=%s unmatched_discards=%s consumed=%s "
                "unmatched_consumes=%s",
                player_name,
                turn_num,
                added_names,
                removed_names,
                discarded_names,
                unmatched_discards,
                consumed_names,
                unmatched_consumes,
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
    # Playtest 2026-04-29: pre-compute the case-folded set of PC names so the
    # registry never admits a name that already belongs to a player character.
    # The MP joiner-orientation auto-narration was naming the host PC in the
    # narration block, and the auto-register loop was promoting that PC into
    # the NPC registry as ``role=ally`` (symptom of the symmetric 45-18 bug).
    # Once a PC is in the NPC registry, downstream beat-selection and party
    # state queries treat them as fungible with NPCs — the narrator and the
    # mechanical layer both stop knowing the player exists as a player.
    pc_name_lookup = {
        c.core.name.lower(): c.core.name
        for c in snapshot.characters
        if getattr(getattr(c, "core", None), "name", None)
    }
    for npc_mention in result.npcs_present:
        matched_pc = pc_name_lookup.get(npc_mention.name.lower())
        if matched_pc is not None:
            with npc_pc_name_skipped_span(
                npc_name=npc_mention.name,
                matched_pc=matched_pc,
                turn_number=turn_num,
            ):
                logger.info(
                    "npc.pc_name_skipped name=%r matched_pc=%r turn=%d",
                    npc_mention.name,
                    matched_pc,
                    turn_num,
                )
            continue
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
            elif cdef.resolution_mode == ResolutionMode.opposed_check:
                # ---- Opposed-check resolution branch (combat fairness, 2026-04-26) ----
                # Both sides roll d20 + modifier; tier comes from the shift.
                # The player's roll arrived via DICE_THROW and is stashed on
                # session_data; ``dispatch_dice_throw`` deferred apply_beat
                # for the player so this branch can derive the tier from the
                # opposing roll instead of the legacy roll-vs-DC tier.
                #
                # Spec: ``.archive/handoffs/opposed-checks-design.md``.
                outcome_obj = _resolve_opposed_check_branch(
                    encounter=enc,
                    cdef=cdef,
                    selections=gated_selections,
                    pack_beats={b.id: b for b in cdef.beats},
                    pending_player_d20=opposed_player_d20,
                    pending_player_beat_id=opposed_player_beat_id,
                    pending_player_actor=opposed_player_actor,
                    turn=snapshot.turn_manager.interaction,
                    snapshot=snapshot,
                )
                if outcome_obj.encounter_resolved:
                    snapshot.pending_resolution_signal = (
                        _build_resolution_signal(enc)
                    )
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
                # Story 45-9: bump total_beats_fired counter + OTEL.
                # Every non-skipped apply_beat is one real beat fire; the
                # campaign-maturity ladder in world_materialization reads
                # this counter, so the increment must be unconditional
                # here (CLAUDE.md no silent fallbacks).
                snapshot.record_beat_fired(
                    beat_id=sel.beat_id,
                    encounter_type=enc.encounter_type,
                    turn=turn_num,
                    source="narrator_beat",
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
                    # Scratch sweep at encounter resolution. Encounter end
                    # is the canonical "scene end" trigger that the Scratch
                    # severity tier promises in game/status.py — without
                    # this sweep, Scratches accumulate forever (Bug #1).
                    from sidequest.server.status_clear import (
                        clear_scratch_on_scene_end,
                    )
                    clear_scratch_on_scene_end(
                        snapshot,
                        reason="scene_end",
                        turn=turn_num,
                    )
                    break

    if result.status_changes:
        from sidequest.game.status import StatusSeverity
        from sidequest.server.status_clear import apply_explicit_status_clears
        turn_num = snapshot.turn_manager.interaction
        encounter_type = (
            snapshot.encounter.encounter_type if snapshot.encounter else None
        )
        # Explicit clears first — process every {"actor": ..., "clear": "<text>"}
        # entry so a single turn can clear an old status and add a new one
        # without the new ADD getting steamrolled. The clear path is the
        # narrator's tool for ending Wound/Scar conditions narratively
        # ("she wriggles free", "the medic binds the gash").
        apply_explicit_status_clears(
            snapshot,
            status_changes=result.status_changes,
            turn=turn_num,
        )
        for entry in result.status_changes:
            # An entry is EITHER a clear OR an add — never both. Clears
            # were handled above; skip them here.
            if entry.get("clear"):
                continue
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
            _append_status_to_actor(
                target=target,
                actor=actor_name,
                text=text,
                severity=severity,
                source="narrator_extraction",
                turn_num=turn_num,
                encounter_type=encounter_type,
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


@dataclass
class _OpposedBranchOutcome:
    """Outcome of one opposed_check dispatch branch — kept terse."""

    encounter_resolved: bool


def _roll_d20_server_side() -> int:
    """Server-side d20 roll for the opponent in an opposed-check turn.

    Recommended in ``.archive/handoffs/opposed-checks-design.md`` §Open
    questions (1): opponent's d20 is server-side, no animation. The
    rolling player keeps their physics-settled die; the opponent's roll
    appears in the result pane after both sides commit.

    Wrapped so tests can monkey-patch the import for deterministic
    coverage of the shift bands without touching the global RNG.
    """
    import random
    return random.randint(1, 20)


def _resolve_opposed_check_branch(
    *,
    encounter,
    cdef,
    selections,
    pack_beats,
    pending_player_d20: int | None,
    pending_player_beat_id: str | None,
    pending_player_actor: str | None,
    turn: int,
    snapshot: GameSnapshot,
) -> _OpposedBranchOutcome:
    """Run the opposed-check dispatch branch.

    Pulls the player's roll + beat from the pending stash (set by
    ``dispatch_dice_throw``), finds the narrator-emitted opponent beat
    in ``selections`` (the SOUL gate will already have dropped any
    PC-side selections — the opponent beat is what survives), rolls the
    opponent's d20 server-side, runs ``resolve_opposed_check`` to derive
    the tier, emits the lie-detector OTEL span, and finally calls
    ``apply_beat`` for both sides with the engine-derived tier.

    Hard-fails-loud (CLAUDE.md no-silent-fallback) when:

    - ``pending_player_*`` are None — opposed_check requires a preceding
      DICE_THROW frame; the legacy narrator-only path is structurally
      ineligible because PC mechanical actions must trace back to an
      explicit player consent.
    - The player's beat_id is not in ``pack_beats``.
    - No opponent-side beat selection is present in ``selections``.
    - The opponent's beat_id is not in ``pack_beats``.
    """
    from sidequest.game.beat_kinds import apply_beat
    from sidequest.game.opposed_check import resolve_opposed_check
    from sidequest.telemetry.spans import (
        encounter_beat_skipped_span,
        encounter_opposed_roll_resolved_span,
        encounter_resolved_span,
    )

    if (
        pending_player_d20 is None
        or pending_player_beat_id is None
        or pending_player_actor is None
    ):
        raise ValueError(
            f"opposed_check encounter {encounter.encounter_type!r} narration "
            f"arrived without a pending DICE_THROW player roll — "
            f"opposed_check is dice-throw-only (no narrator-only path). "
            f"Bug: dispatch_dice_throw should have stashed "
            f"pending_opposed_player_d20 / _beat_id / _actor on session_data."
        )

    player_actor = encounter.find_actor(pending_player_actor)
    if player_actor is None:
        # Fall back to the first player-side actor — same fallback logic
        # the dice dispatcher applies, kept symmetric so a stash mismatch
        # doesn't destroy the resolution.
        player_actor = next(
            (a for a in encounter.actors if a.side == "player"),
            None,
        )
    if player_actor is None:
        raise ValueError(
            f"opposed_check: no player actor found for pending roll "
            f"(actor name {pending_player_actor!r} not in encounter; "
            f"no fallback player-side actor available)"
        )

    player_beat = pack_beats.get(pending_player_beat_id)
    if player_beat is None:
        raise ValueError(
            f"opposed_check: pending player beat_id "
            f"{pending_player_beat_id!r} not in pack beats for encounter "
            f"{encounter.encounter_type!r}"
        )

    opponent_selection = None
    for sel in selections:
        sel_actor = encounter.find_actor(sel.actor)
        if sel_actor is None:
            continue
        if sel_actor.side == "opponent" and not sel_actor.withdrawn:
            opponent_selection = sel
            break

    if opponent_selection is None:
        raise ValueError(
            f"opposed_check: narrator emitted no opponent-side beat "
            f"selection for encounter {encounter.encounter_type!r}. The "
            f"engine cannot derive an opposed tier without an opposing "
            f"beat. (Narrator prompt regression — see narrator gate text "
            f"for opposed_check.)"
        )

    opponent_actor = encounter.find_actor(opponent_selection.actor)
    if opponent_actor is None:
        raise ValueError(
            f"opposed_check: opponent beat selection references actor "
            f"{opponent_selection.actor!r} not in encounter actors"
        )
    opponent_beat = pack_beats.get(opponent_selection.beat_id)
    if opponent_beat is None:
        raise ValueError(
            f"opposed_check: opponent beat_id {opponent_selection.beat_id!r} "
            f"not in pack beats for encounter {encounter.encounter_type!r}"
        )

    opponent_d20 = _roll_d20_server_side()

    roll_result = resolve_opposed_check(
        player_actor=player_actor,
        opponent_actor=opponent_actor,
        player_beat=player_beat,
        opponent_beat=opponent_beat,
        cdef=cdef,
        player_roll=pending_player_d20,
        opponent_roll=opponent_d20,
        encounter=encounter,
    )

    # Emit BEFORE apply_beat so the GM panel reads the resolver inputs
    # paired with the resulting metric_advance spans below.
    with encounter_opposed_roll_resolved_span(
        encounter_type=encounter.encounter_type,
        player_roll=roll_result.player_roll,
        player_mod=roll_result.player_mod,
        opponent_roll=roll_result.opponent_roll,
        opponent_mod=roll_result.opponent_mod,
        shift=roll_result.shift,
        tier=roll_result.tier.value,
    ):
        pass
    _watcher_publish(
        "state_transition",
        {
            "field": "encounter",
            "op": "opposed_roll_resolved",
            "encounter_type": encounter.encounter_type,
            "player_roll": roll_result.player_roll,
            "player_mod": roll_result.player_mod,
            "opponent_roll": roll_result.opponent_roll,
            "opponent_mod": roll_result.opponent_mod,
            "shift": roll_result.shift,
            "tier": roll_result.tier.value,
        },
        component="encounter",
    )

    encounter_resolved = False
    # Apply player beat first (matches threshold-cross order in apply_beat
    # docstring — "player_metric first, then opponent_metric"). The
    # leading "player"/"opponent" label is preserved as a comment-anchor
    # so future readers see the order intent without diving into actor.side.
    for sel_actor, sel_beat, beat_id in (
        # ("player", ...),
        (player_actor, player_beat, pending_player_beat_id),
        # ("opponent", ...),
        (opponent_actor, opponent_beat, opponent_selection.beat_id),
    ):
        applied = apply_beat(
            encounter, sel_actor, sel_beat, roll_result.tier, turn=turn,
        )
        if applied.skipped_reason is not None:
            with encounter_beat_skipped_span(
                reason=applied.skipped_reason,
                actor=sel_actor.name,
                actor_side=sel_actor.side,
                beat_id=beat_id,
            ):
                pass
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "beat_skipped",
                    "reason": applied.skipped_reason,
                    "actor": sel_actor.name,
                    "actor_side": sel_actor.side,
                    "beat_id": beat_id,
                    "source": "opposed_check",
                },
                component="encounter",
            )
            continue
        own_delta = applied.deltas.own if applied.deltas else 0
        opp_delta = applied.deltas.opponent if applied.deltas else 0
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "beat_applied",
                "actor": sel_actor.name,
                "actor_side": sel_actor.side,
                "beat_id": beat_id,
                "beat_kind": (
                    sel_beat.kind.value
                    if hasattr(sel_beat.kind, "value")
                    else str(sel_beat.kind)
                ),
                "outcome_tier": roll_result.tier.value,
                "own_delta": own_delta,
                "opponent_delta": opp_delta,
                "metric_target": encounter.encounter_type,
                "source": "opposed_check",
            },
            component="encounter",
        )
        # Story 45-9: bump total_beats_fired counter + OTEL.
        snapshot.record_beat_fired(
            beat_id=beat_id,
            encounter_type=encounter.encounter_type,
            turn=turn,
            source="opposed_check",
        )
        if applied.resolved:
            with encounter_resolved_span(
                encounter_type=encounter.encounter_type,
                outcome=encounter.outcome or "",
                source="opposed_check",
            ):
                pass
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "resolved",
                    "encounter_type": encounter.encounter_type,
                    "outcome": encounter.outcome or "",
                    "source": "opposed_check",
                    "final_player_metric": encounter.player_metric.current,
                    "final_opponent_metric": encounter.opponent_metric.current,
                },
                component="encounter",
            )
            encounter_resolved = True
            break

    return _OpposedBranchOutcome(encounter_resolved=encounter_resolved)


# ---------------------------------------------------------------------------
# Story 45-20 — trope resolution handshake.
# ---------------------------------------------------------------------------

# Guardrail length for ``active_stakes`` so runaway growth does not pollute
# the next narrator's state_summary prompt. The field is reflected verbatim
# into the prompt JSON; ~1024 chars is the soft cap.
_ACTIVE_STAKES_GUARDRAIL = 1024


def _handshake_resolved_tropes(
    snapshot: GameSnapshot,
    baseline_status: dict[str, str],
    *,
    player_name: str,
    source: str,
) -> None:
    """Diff ``baseline_status`` against the snapshot's current
    ``active_tropes`` and write the durable record for every trope whose
    current status is ``"resolved"``.

    For each detected trope:

    - If the baseline status was anything other than ``"resolved"``
      (including absent — a brand-new resolved trope from chapter
      promotion), this is a fresh resolution: write
      ``quest_log[f"trope_{id}"]`` (wrapped in ``quest_update_span`` so
      the existing GM-panel ``SPAN_QUEST_UPDATE`` route surfaces the
      entry) and append a resolution marker to ``active_stakes``,
      trimming if the field exceeds ``_ACTIVE_STAKES_GUARDRAIL``.
    - If the baseline status was ``"resolved"``, this is an idempotent
      re-detect: no rewrite, but the handshake span still fires with
      ``active_stakes_appended=False`` so the GM panel can distinguish
      "handshake correctly idempotent" from "handshake never engaged
      after turn N".

    The lie-detector contract is that one span fires per detected
    resolved trope, every turn. The bug Orin saw was zero spans firing
    at all.
    """

    interaction = snapshot.turn_manager.interaction
    fresh_writes: dict[str, str] = {}

    for trope in snapshot.active_tropes:
        if trope.status != "resolved":
            continue

        prior = baseline_status.get(trope.id, "")
        is_fresh = prior != "resolved"
        quest_log_key = f"trope_{trope.id}"

        if is_fresh:
            entry_text = f"Resolved at turn {interaction}"
            fresh_writes[quest_log_key] = entry_text

            marker = f"[Resolved: {trope.id} on turn {interaction}]"
            existing = snapshot.active_stakes
            if existing:
                snapshot.active_stakes = f"{existing}\n{marker}"
            else:
                snapshot.active_stakes = marker
            if len(snapshot.active_stakes) > _ACTIVE_STAKES_GUARDRAIL:
                # Trim oldest content but always keep the new marker
                # at the tail — that is the load-bearing field for the
                # next narrator.
                tail = marker
                head_budget = _ACTIVE_STAKES_GUARDRAIL - len(tail) - 1
                head = snapshot.active_stakes[:head_budget]
                snapshot.active_stakes = f"{head}\n{tail}"

        with trope_resolution_handshake_span(
            trope_id=trope.id,
            prior_status=prior,
            new_status="resolved",
            interaction=interaction,
            quest_log_key=quest_log_key,
            active_stakes_appended=is_fresh,
            source=source,
        ):
            pass

    if fresh_writes:
        with quest_update_span(
            updates=fresh_writes,
            player_name=player_name,
            turn_number=interaction,
        ):
            for key, status_text in fresh_writes.items():
                snapshot.quest_log[key] = status_text
            logger.info(
                "trope.resolution_handshake fresh_writes=%d player=%s turn=%d",
                len(fresh_writes),
                player_name,
                interaction,
            )
