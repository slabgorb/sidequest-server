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

from pydantic import BaseModel, Field

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
    snapshot: object,
    pack: object,
    *,
    days_advanced: int,
    now_turn: int,
) -> TropeTimeSkipFields:
    """Advance every progressing trope by ``rate_per_day * clamp(days_advanced, 0, DAY_TICK_CAP)``.

    Fires every crossed beat threshold and appends ``TimeSkipBeatEvent``
    entries to ``snapshot.pending_time_skip_summary``.

    Implementation: T4 (story 50-4).
    """
    raise NotImplementedError("_pass_a2_time_skip — implemented in T4 (story 50-4)")
