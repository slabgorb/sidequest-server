"""Story 50-4 — between-session passive trope advancement (ADR-018).

The in-session engine (``trope_tick.py``) ticks tropes by
``rate_per_turn`` once per narration turn. This engine ticks by
``rate_per_day`` once per session load, closing the "world is alive
between sessions" loop the data model has carried since the port.

Semantics, per the story acceptance criteria:

1. **AC1.** A progressing trope's progress advances by
   ``rate_per_day * elapsed_days`` where ``elapsed_days`` is
   ``(now - snapshot.last_saved_at).total_seconds() / 86400``.
   Dormant and resolved tropes do not advance — dormant must clear
   the in-session activation gate (cap + cooldown), and resolved is
   terminal.

2. **AC2.** Every escalation beat whose threshold lies in
   ``(progress_before, progress_after]`` fires this pass. Unlike the
   in-session engine, between-session does *not* stagger — passive
   catch-up may fire multiple beats at once. The narrator's opening
   turn handles the framing; no NarrativeEntry is appended here.

3. **AC3.** Progress clamps at 1.0. If progress reaches 1.0 AND every
   beat in the escalation has fired, the trope transitions to
   ``resolved``.

4. **AC4.** ``snapshot.last_saved_at = None`` is a no-op — a never-saved
   snapshot has no anchor to compute elapsed-days against, and inventing
   one (e.g. ``now - epoch``) is exactly the silent fallback the project
   forbids.

5. **AC5.** One ``SPAN_TROPE_BETWEEN_SESSION_ADVANCE`` event per trope
   that actually moved, carrying ``trope_id``, ``days_elapsed``,
   ``progress_before``, ``progress_after``, ``beats_fired_count``
   (per-pass, not cumulative), and ``new_status`` (only set when the
   trope just resolved).

Wire site (AC6): ``sidequest.handlers.connect``, after the snapshot is
deserialized and the magic-state backfill runs, before any turn
dispatches.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sidequest.game.session import GameSnapshot
from sidequest.telemetry.spans import (
    SPAN_TROPE_BETWEEN_SESSION_ADVANCE,
    Span,
)

_SECONDS_PER_DAY = 86400.0


def advance_tropes_between_sessions(
    *,
    snapshot: GameSnapshot,
    pack: Any,
    now: datetime,
) -> None:
    """Advance each progressing trope by ``rate_per_day * elapsed_days``.

    Mutates ``snapshot.active_tropes`` in place. ``pack`` is duck-typed
    on ``pack.tropes`` (matches ``tick_tropes``); the engine reads
    ``passive_progression.rate_per_day`` and ``escalation`` from each
    matched ``TropeDefinition``.

    No-op when ``snapshot.last_saved_at`` is ``None`` (first load) or
    set in the future (clock skew). Either case lacks a meaningful
    elapsed-time anchor.
    """

    last_saved = snapshot.last_saved_at
    if last_saved is None:
        return

    delta_seconds = (now - last_saved).total_seconds()
    if delta_seconds <= 0:
        return

    elapsed_days = delta_seconds / _SECONDS_PER_DAY

    pack_tropes_by_id: dict[str, Any] = {
        t.id: t for t in getattr(pack, "tropes", []) if t.id is not None
    }

    for trope in snapshot.active_tropes:
        if trope.status != "progressing":
            continue
        tdef = pack_tropes_by_id.get(trope.id)
        if tdef is None or tdef.passive_progression is None:
            continue
        rate = tdef.passive_progression.rate_per_day or 0.0
        if rate <= 0:
            continue

        progress_before = trope.progress
        progress_after = min(1.0, progress_before + rate * elapsed_days)
        if progress_after <= progress_before:
            continue

        # Fire every beat whose threshold falls in the advance window.
        # Beats are ordered by threshold ascending; once we miss one,
        # the rest are above the window too.
        beats_fired_this_pass = 0
        escalation = tdef.escalation or []
        for beat in escalation[trope.beats_fired :]:
            if progress_before < beat.at <= progress_after:
                trope.beats_fired += 1
                beats_fired_this_pass += 1
            else:
                break

        trope.progress = progress_after

        new_status = ""
        if (
            progress_after >= 1.0
            and trope.beats_fired >= len(escalation)
            and trope.status != "resolved"
        ):
            trope.status = "resolved"
            new_status = "resolved"

        with Span.open(
            SPAN_TROPE_BETWEEN_SESSION_ADVANCE,
            {
                "trope_id": trope.id,
                "days_elapsed": float(elapsed_days),
                "progress_before": float(progress_before),
                "progress_after": float(progress_after),
                "beats_fired_count": int(beats_fired_this_pass),
                "new_status": new_status,
            },
        ):
            pass
