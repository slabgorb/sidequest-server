"""Story 50-4 — time-skip pass for the trope engine.

When the narrator emits a multi-day jump (``days_advanced > 0`` in the
game_patch), this module's ``_pass_a2_time_skip`` advances every
progressing trope by ``rate_per_day * clamp(days, 0, DAY_TICK_CAP)``,
fires every crossed beat threshold, and appends ``TimeSkipBeatEvent``
entries to ``snapshot.pending_time_skip_summary`` for the next
narrator turn to render as a TIME-SKIP CONTEXT block.

See ADR-018 (trope engine) and the design spec at
docs/superpowers/specs/2026-05-13-50-4-trope-rate-per-day-design.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from sidequest.genre.models.tropes import TropeDefinition

if TYPE_CHECKING:
    from sidequest.game.session import GameSnapshot

# Hard cap on days applied per tick. Prevents narrator over-emission
# ("a year passes") from resolving every trope in one turn. Visible in
# OTEL via ``TropeTimeSkipFields.clamped``. Configurable per genre pack
# is a deliberate YAGNI deferral (see spec out-of-scope).
DAY_TICK_CAP: int = 14


class TimeSkipBeatEvent(BaseModel):
    """A single beat that fired during a time-skip pass.

    Queued onto ``Snapshot.pending_time_skip_summary``. The next narrator
    prompt assembly renders these as bullet entries in the TIME-SKIP
    CONTEXT block and then clears the field (one-shot lifecycle).
    """

    model_config = {"extra": "forbid"}

    trope_id: str
    trope_name: str
    beat_index: int
    beat_event: str
    stakes: str
    npcs_involved: list[str] = Field(default_factory=list)
    days_into_skip: int


class TropeTimeSkipFields(BaseModel):
    """OTEL span payload for ``trope.time_skip``.

    Emitted once per ``tick_tropes`` call where ``days_advanced > 0``,
    regardless of whether any beats fired. Zero-beat ticks are useful
    telemetry — they confirm drift happened on a turn with no eligible
    tropes.
    """

    model_config = {"extra": "forbid"}

    days_requested: int
    days_applied: int
    clamped: bool = False
    tropes_affected: list[str] = Field(default_factory=list)
    tropes_skipped_zero_rate: list[str] = Field(default_factory=list)
    beats_fired_count: int = 0
    beats_fired: list[TimeSkipBeatEvent] = Field(default_factory=list)
    resolved_during_skip: list[str] = Field(default_factory=list)


def _pass_a2_time_skip(
    snapshot: GameSnapshot,
    pack_or_tropes_by_id: dict[str, TropeDefinition] | object,
    *,
    days_advanced: int,
    now_turn: int,
) -> TropeTimeSkipFields:
    """Advance every progressing trope by ``rate_per_day * clamp(days_advanced, 0, DAY_TICK_CAP)``.

    Fires every crossed beat threshold and appends ``TimeSkipBeatEvent``
    entries to ``snapshot.pending_time_skip_summary``.

    ``pack_or_tropes_by_id`` accepts either:

    * a ``dict[str, TropeDefinition]`` keyed by id (what ``tick_tropes``
      already builds and passes), or
    * a duck-typed pack carrying ``.tropes`` (what unit tests use, mirroring
      the Pass A pattern from ``_advance_progress``).

    The dual shape keeps both callers ergonomic without forcing test
    fixtures to rebuild the by-id map themselves.
    """

    # Resolve pack-or-dict to a by-id dict. The runtime wire site
    # (``tick_tropes``) already built this once; passing it through avoids
    # a redundant rebuild. Tests pass a ``SimpleNamespace(tropes=[...])``
    # for fixture ergonomics.
    pack_tropes_by_id: dict[str, TropeDefinition]
    if isinstance(pack_or_tropes_by_id, dict):
        pack_tropes_by_id = pack_or_tropes_by_id
    else:
        pack_tropes_by_id = {
            t.id: t
            for t in getattr(pack_or_tropes_by_id, "tropes", [])
            if t.id is not None
        }

    days_applied = max(0, min(days_advanced, DAY_TICK_CAP))

    if days_applied == 0:
        return TropeTimeSkipFields(
            days_requested=days_advanced,
            days_applied=0,
            clamped=False,
        )

    beats_fired_all: list[TimeSkipBeatEvent] = []
    tropes_affected: list[str] = []
    tropes_skipped_zero_rate: list[str] = []
    resolved_during_skip: list[str] = []

    for tstate in snapshot.active_tropes:
        if tstate.status != "progressing":
            continue

        tdef = pack_tropes_by_id.get(tstate.id)
        if tdef is None:
            # Silent skip per spec — stale snapshot after pack rename
            # shouldn't crash the engine. Matches Pass A's behavior.
            continue

        rate = (
            tdef.passive_progression.rate_per_day if tdef.passive_progression else 0.0
        ) or 0.0

        if rate <= 0.0:
            tropes_skipped_zero_rate.append(tstate.id)
            continue

        progress_before = tstate.progress
        progress_after = min(1.0, progress_before + rate * days_applied)

        if progress_after == progress_before:
            # Already pinned at the cap — nothing to advance.
            continue

        tstate.progress = progress_after
        tropes_affected.append(tstate.id)

        for idx, beat in enumerate(tdef.escalation):
            if idx < tstate.beats_fired:
                # Already fired in a prior tick — Lady Ashworth guard.
                continue
            if beat.at > progress_after:
                # Threshold not yet reached this skip.
                continue

            # Day on which this beat crossed (1-indexed, bounded by
            # days_applied). rate > 0 is guaranteed in this branch.
            days_to_cross = max(1, round((beat.at - progress_before) / rate))
            days_into_skip = min(days_to_cross, days_applied)

            beats_fired_all.append(
                TimeSkipBeatEvent(
                    trope_id=tstate.id,
                    trope_name=tdef.name,
                    beat_index=idx,
                    beat_event=beat.event,
                    stakes=beat.stakes,
                    npcs_involved=list(beat.npcs_involved),
                    days_into_skip=days_into_skip,
                )
            )
            tstate.beats_fired = idx + 1
            tstate.last_fired_turn = now_turn

        # Implicit resolution: progress maxed AND every beat fired.
        if progress_after >= 1.0 and tstate.beats_fired >= len(tdef.escalation):
            tstate.status = "resolved"
            resolved_during_skip.append(tstate.id)

    # Chronological order — narrator prompt presents the day-by-day
    # sequence as it would unfold. Ties broken by trope_id for stable
    # determinism across reloads.
    beats_fired_all.sort(key=lambda b: (b.days_into_skip, b.trope_id))

    snapshot.pending_time_skip_summary.extend(beats_fired_all)
    snapshot.days_elapsed += days_applied

    return TropeTimeSkipFields(
        days_requested=days_advanced,
        days_applied=days_applied,
        clamped=(days_advanced > DAY_TICK_CAP),
        tropes_affected=tropes_affected,
        tropes_skipped_zero_rate=tropes_skipped_zero_rate,
        beats_fired_count=len(beats_fired_all),
        beats_fired=beats_fired_all,
        resolved_during_skip=resolved_during_skip,
    )
