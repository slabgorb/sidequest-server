"""Story 50-4 — Snapshot fields and persistence round-trip for time-skip state.

Verifies:
* ``GameSnapshot`` gains ``days_elapsed`` and ``pending_time_skip_summary``
  with sensible defaults.
* JSON round-trip through ``model_dump_json`` / ``model_validate`` preserves
  both fields. (Persistence in this codebase is whole-snapshot JSON via
  ``SessionPersistence.save``/``load`` — there is no per-field column or
  schema_version bump despite the spec text. Pydantic's default-factory plus
  ``extra='ignore'`` on ``GameSnapshot`` covers legacy-save migration without
  ALTER TABLE — that deviation is logged in the session file.)
* Legacy save payloads (missing both fields) deserialize cleanly with defaults.
* ``StateDelta`` flags changes in either field via ``compute_delta``.

ACs covered: AC-5 (``days_elapsed`` accumulates and persists),
AC-6 (``pending_time_skip_summary`` populated),
AC-10 (existing saves load cleanly).
"""

from __future__ import annotations

from sidequest.game.delta import compute_delta, snapshot
from sidequest.game.session import GameSnapshot
from sidequest.game.trope_time_skip import TimeSkipBeatEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    *,
    trope_id: str = "murder_mystery_clock",
    beat_index: int = 1,
    beat_event: str = "Another body found, identically posed",
    stakes: str = "high",
    npcs: tuple[str, ...] = ("constable_finch",),
    days_into_skip: int = 2,
) -> TimeSkipBeatEvent:
    return TimeSkipBeatEvent(
        trope_id=trope_id,
        trope_name=trope_id.replace("_", " ").title(),
        beat_index=beat_index,
        beat_event=beat_event,
        stakes=stakes,
        npcs_involved=list(npcs),
        days_into_skip=days_into_skip,
    )


# ---------------------------------------------------------------------------
# Snapshot field defaults
# ---------------------------------------------------------------------------


def test_snapshot_default_days_elapsed_is_zero() -> None:
    """A fresh GameSnapshot reports ``days_elapsed == 0``.

    The field must be a monotonic in-game day counter. Default 0 means a
    brand-new session reads as "day zero" — the time-skip pass has not
    yet advanced anything.
    """
    snap = GameSnapshot()
    assert snap.days_elapsed == 0


def test_snapshot_default_pending_time_skip_summary_is_empty() -> None:
    """A fresh GameSnapshot has no pending TIME-SKIP CONTEXT entries.

    The narrator prompt builder treats an empty list as the no-op case
    (don't render the TIME-SKIP CONTEXT block). The field MUST be a
    list, not None — the prompt builder iterates without a None check.
    """
    snap = GameSnapshot()
    assert snap.pending_time_skip_summary == []
    # List, not None — prompt builder iterates without None guard.
    assert isinstance(snap.pending_time_skip_summary, list)


def test_snapshot_carries_days_elapsed_and_summary() -> None:
    """Mutating the new fields holds (no read-only / immutability surprises)."""
    snap = GameSnapshot()
    snap.days_elapsed = 7
    snap.pending_time_skip_summary.append(_make_event())
    assert snap.days_elapsed == 7
    assert len(snap.pending_time_skip_summary) == 1
    assert snap.pending_time_skip_summary[0].trope_id == "murder_mystery_clock"
    assert snap.pending_time_skip_summary[0].days_into_skip == 2


# ---------------------------------------------------------------------------
# JSON round-trip (persistence is whole-snapshot model_dump_json/model_validate)
# ---------------------------------------------------------------------------


def test_snapshot_days_elapsed_round_trips_through_json() -> None:
    """``model_dump_json`` → ``model_validate`` preserves days_elapsed.

    This is the persistence contract: ``SessionPersistence.save`` writes
    ``snapshot.model_dump_json()`` to ``game_state.snapshot_json`` and
    ``.load`` calls ``GameSnapshot.model_validate``. A regression here
    means a save round-trip would lose the counter.
    """
    snap = GameSnapshot()
    snap.days_elapsed = 14
    blob = snap.model_dump_json()
    loaded = GameSnapshot.model_validate_json(blob)
    assert loaded.days_elapsed == 14


def test_snapshot_pending_summary_round_trips_through_json() -> None:
    """Each TimeSkipBeatEvent survives the JSON round-trip with every field."""
    snap = GameSnapshot()
    snap.pending_time_skip_summary.extend(
        [
            _make_event(beat_event="Day 2 — body found", days_into_skip=2),
            _make_event(
                trope_id="gossip_propagation",
                beat_index=0,
                beat_event="Rumor spreads beyond household",
                stakes="medium",
                npcs=("maid_dorothy", "vicar_pell"),
                days_into_skip=4,
            ),
        ]
    )
    blob = snap.model_dump_json()
    loaded = GameSnapshot.model_validate_json(blob)
    assert len(loaded.pending_time_skip_summary) == 2
    first, second = loaded.pending_time_skip_summary
    assert first.beat_event == "Day 2 — body found"
    assert first.days_into_skip == 2
    assert second.trope_id == "gossip_propagation"
    assert second.stakes == "medium"
    assert second.npcs_involved == ["maid_dorothy", "vicar_pell"]


# ---------------------------------------------------------------------------
# Legacy-save compatibility (no ALTER TABLE — JSON defaults handle it)
# ---------------------------------------------------------------------------


def test_legacy_snapshot_without_new_fields_loads_with_defaults() -> None:
    """A snapshot payload that predates 50-4 must load with defaults.

    ``GameSnapshot.model_config['extra'] == 'ignore'`` means unknown
    keys are dropped; field-with-default semantics give missing keys
    their factory value. Together: legacy saves load cleanly without
    a schema bump. (This replaces the spec's ALTER TABLE design.)
    """
    minimal = {"genre_slug": "tea_and_murder", "world_slug": "ashworth_manor"}
    loaded = GameSnapshot.model_validate(minimal)
    assert loaded.days_elapsed == 0
    assert loaded.pending_time_skip_summary == []


# ---------------------------------------------------------------------------
# StateDelta wiring (in-process change detection for reactive state)
# ---------------------------------------------------------------------------


def test_state_delta_flags_days_elapsed_change() -> None:
    """A change in ``days_elapsed`` shows up on the delta's flag.

    Without this wire, reactive state messaging (ADR-027) would not
    surface day-counter advancement to the client mirror — the Day N
    indicator would never update mid-session.
    """
    before_snap = GameSnapshot()
    after_snap = GameSnapshot()
    after_snap.days_elapsed = 5

    delta = compute_delta(snapshot(before_snap), snapshot(after_snap))
    assert delta.days_elapsed is True


def test_state_delta_flags_pending_summary_change() -> None:
    """A new TimeSkipBeatEvent appended to the summary flips the delta flag."""
    before_snap = GameSnapshot()
    after_snap = GameSnapshot()
    after_snap.pending_time_skip_summary.append(_make_event())

    delta = compute_delta(snapshot(before_snap), snapshot(after_snap))
    assert delta.pending_time_skip_summary is True


def test_state_delta_quiet_when_time_skip_state_unchanged() -> None:
    """Both flags stay False when the time-skip state is identical.

    Guards against an over-eager always-True flag that would spam the
    client mirror with no-op updates.
    """
    before_snap = GameSnapshot()
    before_snap.days_elapsed = 3
    before_snap.pending_time_skip_summary.append(_make_event())
    after_snap = GameSnapshot()
    after_snap.days_elapsed = 3
    after_snap.pending_time_skip_summary.append(_make_event())

    delta = compute_delta(snapshot(before_snap), snapshot(after_snap))
    assert delta.days_elapsed is False
    assert delta.pending_time_skip_summary is False
