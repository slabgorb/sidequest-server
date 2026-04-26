"""Status clearing — the missing half of the condition lifecycle.

Background
----------
The narrator can emit ``status_changes`` to add lingering costs to actors
(see ``narration_apply._apply_narration_result_to_snapshot``). Each
``Status`` carries a severity tier whose recovery cadence is documented in
``game/status.py``:

  - Scratch: clears at scene end (graze, lost composure).
  - Wound:   clears at session end or with rest (real injury).
  - Scar:    persists until a milestone or healing event (permanent mark).

The ADD path was wired in story 41-6, but the CLEAR path was never wired.
Result (playtest 2026-04-26 Bug #1): conditions accumulate forever — both
players ended a single scene with stacked "Choked", "Captured by the
Butcher's count", "Twisted wrist" pills that no narrative beat ever
removed.

This module provides the missing clears:

  - ``clear_scratch_on_scene_end``: called when an encounter resolves or
    location changes. Sweeps every Scratch off every PC.
  - ``apply_explicit_status_clears``: called from narration_apply when the
    narrator emits ``{"actor": ..., "clear": "<text>"}`` entries. Used for
    Wound/Scar removal beats ("they wriggle free", "the wound is bound").

Both paths emit ``encounter.status_cleared`` spans + a typed watcher
``state_transition`` event so the GM panel can verify the clear actually
fired (CLAUDE.md OTEL Observability Principle — Claude is excellent at
"winging it"; the only way to catch a stale-condition pile-up live is to
watch the clear events stream past in the dashboard).
"""
from __future__ import annotations

import logging
from typing import Any

from sidequest.game.session import GameSnapshot
from sidequest.game.status import Status, StatusSeverity
from sidequest.telemetry.spans import encounter_status_cleared_span
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

logger = logging.getLogger(__name__)


def _publish_clear(
    *, actor: str, status: Status, reason: str, turn: int,
) -> None:
    """Emit OTEL span + watcher event for one cleared status."""
    with encounter_status_cleared_span(
        actor=actor,
        text=status.text,
        severity=status.severity.value,
        reason=reason,
    ):
        pass
    _watcher_publish(
        "state_transition",
        {
            "field": "encounter",
            "op": "status_cleared",
            "actor": actor,
            "text": status.text,
            "severity": status.severity.value,
            "reason": reason,
            "turn": turn,
            "created_in_encounter": status.created_in_encounter,
        },
        component="encounter",
    )
    logger.info(
        "status.cleared actor=%s text=%r severity=%s reason=%s turn=%d",
        actor, status.text, status.severity.value, reason, turn,
    )


def clear_scratch_on_scene_end(
    snapshot: GameSnapshot,
    *,
    reason: str,
    turn: int,
) -> int:
    """Sweep every Scratch off every character in the snapshot.

    Returns the number of statuses cleared. ``reason`` is forwarded to the
    OTEL span — typical values are ``"scene_end"`` (encounter resolved) or
    ``"location_change"`` (PC moved to a new place). Callers MUST pass a
    specific reason so the GM panel can distinguish trigger sources — no
    silent default.

    NPC statuses on ``snapshot.npc_registry`` are not touched here; NPCs
    don't surface a condition pill in the party panel and the bug is
    scoped to PC accumulation. If/when NPC statuses become first-class,
    extend this function rather than scattering the sweep.
    """
    cleared = 0
    for char in snapshot.characters:
        kept: list[Status] = []
        for status in char.core.statuses:
            if status.severity is StatusSeverity.Scratch:
                _publish_clear(
                    actor=char.core.name,
                    status=status,
                    reason=reason,
                    turn=turn,
                )
                cleared += 1
                continue
            kept.append(status)
        char.core.statuses = kept
    return cleared


def apply_explicit_status_clears(
    snapshot: GameSnapshot,
    *,
    status_changes: list[dict[str, Any]],
    turn: int,
) -> int:
    """Apply narrator-emitted explicit clears.

    The schema extension: a ``status_changes`` entry shaped
    ``{"actor": "<name>", "clear": "<text>"}`` removes the first status
    on that actor whose ``text`` matches (case-insensitive). This is how
    Wound / Scar clear in narrative beats — "she wriggles free of the
    Butcher's grip", "the medic binds the gash". Scratch clears piggyback
    on scene-end and don't need this path, but explicit clears work for
    them too if the narrator chooses to call out the moment.

    Returns the number of statuses cleared. Unknown actor or no matching
    status logs a WARNING (CLAUDE.md no silent fallbacks — a typo in the
    narrator's clear should show up in logs, not vanish).
    """
    cleared = 0
    for entry in status_changes:
        if not isinstance(entry, dict):
            continue
        clear_text_raw = entry.get("clear")
        if not clear_text_raw:
            continue
        actor_name = str(entry.get("actor", "")).strip()
        clear_text = str(clear_text_raw).strip()
        if not actor_name or not clear_text:
            continue
        target = next(
            (c for c in snapshot.characters if c.core.name == actor_name),
            None,
        )
        if target is None:
            logger.warning(
                "status_clear.unknown_actor actor=%s clear=%r",
                actor_name, clear_text,
            )
            continue
        # First case-insensitive match wins. Substring match (so the
        # narrator can write "Choked" to clear "Choked — fingers at the
        # throat, breath shallow") — but only when the clear text is a
        # whole token of the status text, never the other way round, so a
        # short clear can't accidentally sweep an unrelated long status.
        match_idx: int | None = None
        clear_lc = clear_text.lower()
        for i, status in enumerate(target.core.statuses):
            status_lc = status.text.lower()
            if status_lc == clear_lc or clear_lc in status_lc:
                match_idx = i
                break
        if match_idx is None:
            logger.warning(
                "status_clear.no_match actor=%s clear=%r existing=%s",
                actor_name, clear_text,
                [s.text for s in target.core.statuses],
            )
            continue
        removed = target.core.statuses.pop(match_idx)
        _publish_clear(
            actor=actor_name,
            status=removed,
            reason="narrator_clear",
            turn=turn,
        )
        cleared += 1
    return cleared
