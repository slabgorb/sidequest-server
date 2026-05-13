"""Story 50-4 — Pass A2 time-skip algorithm tests.

Verifies the new ``_pass_a2_time_skip`` pass (between Pass A and Pass B in
``tick_tropes``) advances every progressing trope by
``rate_per_day * clamp(days_advanced, 0, DAY_TICK_CAP)``, fires every crossed
beat threshold, and queues ``TimeSkipBeatEvent`` entries onto
``snapshot.pending_time_skip_summary`` for the next narrator turn.

ACs covered: AC-3 (progress advancement), AC-4 (multi-beat fire),
AC-5 (days_elapsed += clamped), AC-6 (summary sorted),
AC-7 (TimeSkipSpanFields shape), AC-11 (zero-rate no-op),
AC-12 (Pass B interaction).

Test fixture pattern mirrors ``tests/game/test_trope_tick.py`` —
``SimpleNamespace(tropes=[...])`` for the pack, ``_seed_snapshot`` builder.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.session import GameSnapshot, TropeState
from sidequest.game.trope_time_skip import (
    DAY_TICK_CAP,
    TimeSkipBeatEvent,
    TropeTimeSkipFields,
    _pass_a2_time_skip,
)
from sidequest.genre.models.tropes import (
    PassiveProgression,
    TropeDefinition,
    TropeEscalation,
)
from sidequest.telemetry.setup import init_tracer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trope_def(
    trope_id: str = "t",
    *,
    rate_per_day: float = 0.04,
    rate_per_turn: float = 0.0,
    thresholds: tuple[float, ...] = (0.25, 0.50, 0.75, 1.00),
    stakes: str = "high",
    npcs_involved: tuple[str, ...] = (),
) -> TropeDefinition:
    """Build a deterministic TropeDefinition with a known rate_per_day."""

    escalation = [
        TropeEscalation(
            at=t,
            event=f"beat-{i} at {t}",
            stakes=stakes,
            npcs_involved=list(npcs_involved),
        )
        for i, t in enumerate(thresholds)
    ]
    return TropeDefinition(
        id=trope_id,
        name=trope_id.replace("_", " ").title(),
        category="tension",
        escalation=escalation,
        passive_progression=PassiveProgression(
            rate_per_turn=rate_per_turn,
            rate_per_day=rate_per_day,
        ),
    )


def _pack_with(tropes: list[TropeDefinition]) -> SimpleNamespace:
    """Duck-typed pack carrying only ``.tropes`` (same shape Pass A consumes)."""
    return SimpleNamespace(tropes=tropes)


def _seed_snapshot(states: list[tuple[str, str, float]]) -> GameSnapshot:
    """Build a snapshot with ``active_tropes = [(id, status, progress), ...]``."""
    snap = GameSnapshot(genre_slug="tea_and_murder")
    for trope_id, status, progress in states:
        snap.active_tropes.append(
            TropeState(id=trope_id, status=status, progress=progress, beats_fired=0)
        )
    snap.turn_manager.interaction = 10
    return snap


@pytest.fixture
def otel_capture():
    """In-memory span exporter for asserting on emitted spans."""
    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()
        exporter.clear()


# ---------------------------------------------------------------------------
# Module-level constants and exports
# ---------------------------------------------------------------------------


class TestModuleSurface:
    """The new module exposes a small, named surface — DAY_TICK_CAP,
    TimeSkipBeatEvent, TropeTimeSkipFields, _pass_a2_time_skip — so other
    callers (trope_tick, narration_apply, narrator prompt builder) can
    import them without grepping for inlined literals (ADR-068).
    """

    def test_day_tick_cap_is_14(self) -> None:
        """The spec locks the per-tick cap at 14 days. Changes to this
        constant change game pacing — pin the contract so a casual tweak
        doesn't silently resolve every trope in a year-long skip.
        """
        assert DAY_TICK_CAP == 14

    def test_time_skip_beat_event_fields(self) -> None:
        """TimeSkipBeatEvent carries every field the prompt builder
        needs to render the TIME-SKIP CONTEXT block.
        """
        ev = TimeSkipBeatEvent(
            trope_id="t",
            trope_name="T",
            beat_index=0,
            beat_event="something happened",
            stakes="high",
            npcs_involved=["npc_a"],
            days_into_skip=3,
        )
        assert ev.trope_id == "t"
        assert ev.beat_event == "something happened"
        assert ev.days_into_skip == 3
        assert ev.npcs_involved == ["npc_a"]

    def test_time_skip_beat_event_rejects_unknown_fields(self) -> None:
        """``extra='forbid'`` — typo in field name fails fast instead
        of silently dropping the value (spec design decision).
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TimeSkipBeatEvent(
                trope_id="t",
                trope_name="T",
                beat_index=0,
                beat_event="x",
                stakes="high",
                days_into_skip=1,
                bogus_field="oops",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Pass A2 — no-op & guard cases
# ---------------------------------------------------------------------------


class TestPassA2NoOp:
    """``_pass_a2_time_skip`` is guarded by ``days_advanced > 0`` and
    skips conditions that have no business advancing during a time skip.
    """

    def test_no_op_when_days_zero(self) -> None:
        """``days_advanced=0`` returns early — no mutation, no work."""
        snap = _seed_snapshot([("t", "progressing", 0.10)])
        pack = _pack_with([_trope_def()])

        fields = _pass_a2_time_skip(snap, pack, days_advanced=0, now_turn=11)

        assert fields.days_applied == 0
        assert fields.beats_fired_count == 0
        assert snap.active_tropes[0].progress == 0.10
        assert snap.days_elapsed == 0
        assert snap.pending_time_skip_summary == []

    def test_skips_dormant_trope(self) -> None:
        """Dormant tropes do not advance during a time skip — they
        haven't been activated and rate_per_day applies only to
        progressing tropes.
        """
        snap = _seed_snapshot([("t", "dormant", 0.50)])
        pack = _pack_with([_trope_def(rate_per_day=0.04)])

        fields = _pass_a2_time_skip(snap, pack, days_advanced=14, now_turn=11)

        assert snap.active_tropes[0].progress == 0.50
        assert "t" not in fields.tropes_affected

    def test_skips_resolved_trope(self) -> None:
        """Resolved tropes are terminal — Pass A2 must not touch them."""
        snap = _seed_snapshot([("t", "resolved", 1.0)])
        pack = _pack_with([_trope_def(rate_per_day=0.04)])

        fields = _pass_a2_time_skip(snap, pack, days_advanced=14, now_turn=11)

        assert snap.active_tropes[0].progress == 1.0
        assert "t" not in fields.tropes_affected

    def test_zero_rate_no_op_even_with_days(self) -> None:
        """Caverns_and_claudes pattern: ``rate_per_day=0.0`` means no drift,
        even on a 14-day skip. Confirms AC-11.

        ``days_elapsed`` still advances — wall-time passes for the world
        even if no trope tracks daily progression.
        """
        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with([_trope_def(rate_per_day=0.0)])

        fields = _pass_a2_time_skip(snap, pack, days_advanced=14, now_turn=11)

        assert snap.active_tropes[0].progress == 0.0
        assert "t" in fields.tropes_skipped_zero_rate
        assert "t" not in fields.tropes_affected
        assert snap.days_elapsed == 14  # day counter still advances

    def test_missing_trope_def_is_skipped(self) -> None:
        """A TropeState whose id isn't in the pack is skipped silently
        (matches Pass A's lookup-miss behavior). Avoids a hard crash on
        a stale snapshot loaded after a pack rename.
        """
        snap = _seed_snapshot([("orphan", "progressing", 0.10)])
        pack = _pack_with([])  # no matching def

        fields = _pass_a2_time_skip(snap, pack, days_advanced=7, now_turn=11)

        # No mutation, no crash.
        assert snap.active_tropes[0].progress == 0.10
        assert "orphan" not in fields.tropes_affected


# ---------------------------------------------------------------------------
# Pass A2 — progress advancement and day clamping
# ---------------------------------------------------------------------------


class TestPassA2Advancement:
    """AC-3: progress += rate_per_day * clamp(days, 0, DAY_TICK_CAP)."""

    def test_advances_progress_linearly(self) -> None:
        """5 days × rate 0.04 = +0.20, applied on top of 0.10 → 0.30."""
        snap = _seed_snapshot([("t", "progressing", 0.10)])
        pack = _pack_with([_trope_def(rate_per_day=0.04, thresholds=(0.99,))])

        fields = _pass_a2_time_skip(snap, pack, days_advanced=5, now_turn=11)

        assert snap.active_tropes[0].progress == pytest.approx(0.30, abs=1e-9)
        assert fields.days_applied == 5
        assert "t" in fields.tropes_affected
        assert fields.clamped is False

    def test_progress_caps_at_one(self) -> None:
        """``min(1.0, progress + rate*days)`` — never overshoots 1.0."""
        snap = _seed_snapshot([("t", "progressing", 0.90)])
        pack = _pack_with([_trope_def(rate_per_day=0.05, thresholds=(0.99,))])

        _pass_a2_time_skip(snap, pack, days_advanced=14, now_turn=11)

        assert snap.active_tropes[0].progress == 1.0

    def test_clamps_days_at_day_tick_cap(self) -> None:
        """A "year passes" emission clamps to DAY_TICK_CAP=14. Visible
        in OTEL via ``clamped=True``. Confirms the spec's drift cap.
        """
        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with([_trope_def(rate_per_day=0.04, thresholds=(0.99,))])

        fields = _pass_a2_time_skip(snap, pack, days_advanced=365, now_turn=11)

        assert fields.days_applied == DAY_TICK_CAP
        assert fields.days_requested == 365
        assert fields.clamped is True
        # 0.0 + 0.04 * 14 = 0.56
        assert snap.active_tropes[0].progress == pytest.approx(0.56, abs=1e-9)

    def test_days_elapsed_accumulates_clamped_value(self) -> None:
        """AC-5: ``days_elapsed`` advances by ``days_applied``, not
        ``days_requested`` — over-emission clamps cleanly without
        leaving a phantom day count.
        """
        snap = _seed_snapshot([("t", "progressing", 0.0)])
        snap.days_elapsed = 100  # prior counter
        pack = _pack_with([_trope_def(rate_per_day=0.04)])

        _pass_a2_time_skip(snap, pack, days_advanced=365, now_turn=11)

        assert snap.days_elapsed == 100 + DAY_TICK_CAP

    def test_days_elapsed_accumulates_unclamped_value(self) -> None:
        """``days_advanced`` below the cap accumulates directly."""
        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with([_trope_def(rate_per_day=0.04)])

        _pass_a2_time_skip(snap, pack, days_advanced=7, now_turn=11)

        assert snap.days_elapsed == 7


# ---------------------------------------------------------------------------
# Pass A2 — beat firing
# ---------------------------------------------------------------------------


class TestPassA2BeatFiring:
    """AC-4: every crossed beat threshold fires during a single time skip."""

    def test_fires_single_crossed_beat(self) -> None:
        """Progress 0.20 + 0.04*7 = 0.48 crosses the 0.25 beat once."""
        snap = _seed_snapshot([("t", "progressing", 0.20)])
        pack = _pack_with(
            [
                _trope_def(
                    rate_per_day=0.04,
                    thresholds=(0.25, 0.60, 1.0),
                    stakes="high",
                    npcs_involved=("constable_finch",),
                )
            ]
        )

        fields = _pass_a2_time_skip(snap, pack, days_advanced=7, now_turn=11)

        assert fields.beats_fired_count == 1
        assert len(fields.beats_fired) == 1
        event = fields.beats_fired[0]
        assert event.trope_id == "t"
        assert event.beat_index == 0
        assert event.stakes == "high"
        assert "constable_finch" in event.npcs_involved
        assert snap.active_tropes[0].beats_fired == 1
        assert snap.active_tropes[0].last_fired_turn == 11
        assert len(snap.pending_time_skip_summary) == 1

    def test_fires_multiple_crossed_beats(self) -> None:
        """14-day skip × rate 0.04 = +0.56 progress; 4 thresholds at
        0.10/0.30/0.50/0.80 → first 3 cross, 4th doesn't.
        """
        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with(
            [_trope_def(rate_per_day=0.04, thresholds=(0.10, 0.30, 0.50, 0.80))]
        )

        fields = _pass_a2_time_skip(snap, pack, days_advanced=14, now_turn=11)

        assert fields.beats_fired_count == 3
        fired_indices = sorted(b.beat_index for b in fields.beats_fired)
        assert fired_indices == [0, 1, 2]
        assert snap.active_tropes[0].beats_fired == 3
        # last_fired_turn is the now_turn of the tick — same for all crossed beats.
        assert snap.active_tropes[0].last_fired_turn == 11

    def test_summary_sorted_by_days_into_skip_then_trope_id(self) -> None:
        """AC-6: pending_time_skip_summary stored in chronological order
        so the narrator prompt presents the day-by-day sequence right.
        """
        snap = _seed_snapshot(
            [("alpha", "progressing", 0.0), ("bravo", "progressing", 0.0)]
        )
        pack = _pack_with(
            [
                _trope_def("alpha", rate_per_day=0.05, thresholds=(0.30,)),
                _trope_def("bravo", rate_per_day=0.05, thresholds=(0.20,)),
            ]
        )

        _pass_a2_time_skip(snap, pack, days_advanced=10, now_turn=11)

        summary = snap.pending_time_skip_summary
        assert summary == sorted(
            summary, key=lambda b: (b.days_into_skip, b.trope_id)
        )

    def test_does_not_refire_already_fired_beats(self) -> None:
        """A trope that already fired beat-0 in a prior tick does NOT
        re-fire it. Guards against the Lady-Ashworth bug — fire counts
        only forward.
        """
        snap = _seed_snapshot([("t", "progressing", 0.50)])
        snap.active_tropes[0].beats_fired = 1  # beat-0 already fired
        pack = _pack_with(
            [_trope_def(rate_per_day=0.05, thresholds=(0.25, 0.70))]
        )
        # 5 days * 0.05 = +0.25 -> 0.75, crosses 0.70 only.
        fields = _pass_a2_time_skip(snap, pack, days_advanced=5, now_turn=11)

        assert fields.beats_fired_count == 1
        assert fields.beats_fired[0].beat_index == 1  # the second beat
        assert snap.active_tropes[0].beats_fired == 2

    def test_appends_to_existing_pending_summary(self) -> None:
        """A prior tick's queued summary is preserved; new beats append.
        (Pass A2 extends; the prompt builder is what clears.)
        """
        snap = _seed_snapshot([("t", "progressing", 0.20)])
        prior_event = TimeSkipBeatEvent(
            trope_id="other",
            trope_name="Other",
            beat_index=0,
            beat_event="prior beat",
            stakes="low",
            npcs_involved=[],
            days_into_skip=1,
        )
        snap.pending_time_skip_summary.append(prior_event)
        pack = _pack_with([_trope_def(rate_per_day=0.04, thresholds=(0.25,))])

        _pass_a2_time_skip(snap, pack, days_advanced=7, now_turn=11)

        assert len(snap.pending_time_skip_summary) == 2
        assert snap.pending_time_skip_summary[0].beat_event == "prior beat"

    def test_days_into_skip_is_bounded_and_positive(self) -> None:
        """Every fired beat reports ``1 <= days_into_skip <= days_applied``.

        Zero or negative values would scramble the narrator prompt
        ordering; values past ``days_applied`` would imply the beat
        fired AFTER the skip ended, which is incoherent.
        """
        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with(
            [_trope_def(rate_per_day=0.04, thresholds=(0.10, 0.30, 0.50))]
        )

        fields = _pass_a2_time_skip(snap, pack, days_advanced=14, now_turn=11)

        for beat in fields.beats_fired:
            assert 1 <= beat.days_into_skip <= 14


# ---------------------------------------------------------------------------
# Pass A2 — implicit resolution
# ---------------------------------------------------------------------------


class TestPassA2ImplicitResolution:
    """A trope whose progress reaches 1.0 AND whose every beat has fired
    implicitly resolves during the time skip (carries the same
    semantics as a resolved status from Pass B).
    """

    def test_implicit_resolution_when_all_beats_fire_and_progress_maxes(self) -> None:
        """rate 0.1 × 14 days = +1.4 (capped at 1.0), all 3 beats cross."""
        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with(
            [_trope_def(rate_per_day=0.1, thresholds=(0.25, 0.50, 1.0))]
        )

        fields = _pass_a2_time_skip(snap, pack, days_advanced=14, now_turn=11)

        assert snap.active_tropes[0].progress == 1.0
        assert snap.active_tropes[0].beats_fired == 3
        assert snap.active_tropes[0].status == "resolved"
        assert "t" in fields.resolved_during_skip

    def test_no_resolution_when_progress_caps_but_beats_remain(self) -> None:
        """Progress can max while at least one beat stays unfired —
        trope stays ``progressing``. Resolution requires BOTH "progress
        >= 1.0" AND "every beat fired".

        Shape: rate 0.5, one day → +0.5 progress on top of 0.49 = 0.99
        (does NOT cross the 1.0 beat). Crosses 0.50 and 0.51 only.
        beats_fired ends at 2 of 3 → no implicit resolution.
        """
        snap = _seed_snapshot([("t", "progressing", 0.49)])
        snap.active_tropes[0].beats_fired = 0
        pack = _pack_with(
            [_trope_def(rate_per_day=0.5, thresholds=(0.50, 0.51, 1.0))]
        )

        fields = _pass_a2_time_skip(snap, pack, days_advanced=1, now_turn=11)

        assert snap.active_tropes[0].status == "progressing"
        assert "t" not in fields.resolved_during_skip
        assert snap.active_tropes[0].beats_fired == 2  # 2 of 3 fired


# ---------------------------------------------------------------------------
# Pass A2 — OTEL surface (returned fields shape — emission tested at wire level)
# ---------------------------------------------------------------------------


class TestPassA2OtelFields:
    """AC-8: the returned ``TropeTimeSkipFields`` carries every field the
    GM panel needs to render the +Nd badge and the Day N indicator.

    Span EMISSION is the responsibility of ``tick_tropes``; this class
    pins the SHAPE of the payload it will emit.
    """

    def test_fields_carry_full_payload_on_active_skip(self) -> None:
        snap = _seed_snapshot(
            [("alpha", "progressing", 0.0), ("zero", "progressing", 0.0)]
        )
        pack = _pack_with(
            [
                _trope_def("alpha", rate_per_day=0.04, thresholds=(0.10,)),
                _trope_def("zero", rate_per_day=0.0),
            ]
        )

        fields = _pass_a2_time_skip(snap, pack, days_advanced=7, now_turn=11)

        assert isinstance(fields, TropeTimeSkipFields)
        assert fields.days_requested == 7
        assert fields.days_applied == 7
        assert fields.clamped is False
        assert "alpha" in fields.tropes_affected
        assert "zero" in fields.tropes_skipped_zero_rate
        assert fields.beats_fired_count == len(fields.beats_fired)

    def test_beats_fired_count_matches_list_length(self) -> None:
        """Invariant — the count field must always equal the list length.

        A drift here would mean the GM panel's summary number disagrees
        with the per-beat detail it lists.
        """
        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with(
            [_trope_def(rate_per_day=0.04, thresholds=(0.10, 0.30, 0.50, 0.80))]
        )

        fields = _pass_a2_time_skip(snap, pack, days_advanced=14, now_turn=11)

        assert fields.beats_fired_count == len(fields.beats_fired)


# ---------------------------------------------------------------------------
# tick_tropes wiring — Pass A2 runs between Pass A and Pass B
# ---------------------------------------------------------------------------


class TestTickTropesWireUp:
    """``tick_tropes`` must accept ``days_advanced`` and invoke Pass A2.

    This is AC-2's mirror at the engine boundary: the public
    ``tick_tropes(snapshot, pack, *, now_turn, days_advanced)`` shape
    is the contract narration_apply expects.
    """

    def test_tick_tropes_accepts_days_advanced_kwarg(self) -> None:
        """The new ``days_advanced`` keyword defaults to 0 so existing
        call sites continue to compile without change. days_advanced=0
        must produce no Pass A2 work (no progress, no day-counter bump,
        no summary).
        """
        from sidequest.game.trope_tick import tick_tropes

        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with([_trope_def(rate_per_day=0.04, thresholds=(0.99,))])

        # Default — no days_advanced kwarg, behaves like prior tick.
        tick_tropes(snap, pack, now_turn=11)
        assert snap.days_elapsed == 0
        assert snap.pending_time_skip_summary == []
        assert snap.active_tropes[0].progress == 0.0

        # Explicit zero — kwarg accepted, still no Pass A2 work.
        tick_tropes(snap, pack, now_turn=12, days_advanced=0)
        assert snap.days_elapsed == 0
        assert snap.pending_time_skip_summary == []
        assert snap.active_tropes[0].progress == 0.0

    def test_tick_tropes_runs_pass_a2_when_days_positive(self) -> None:
        """A 7-day skip advances a progressing trope's progress AND fires
        the threshold via Pass A2 (Pass B alone would only fire one
        beat per tick — confirming A2 ran).
        """
        from sidequest.game.trope_tick import tick_tropes

        snap = _seed_snapshot([("t", "progressing", 0.20)])
        # rate_per_turn=0 so Pass A does nothing — any change must be from A2.
        pack = _pack_with(
            [
                _trope_def(
                    rate_per_day=0.04,
                    rate_per_turn=0.0,
                    thresholds=(0.25, 0.60),
                )
            ]
        )

        tick_tropes(snap, pack, now_turn=11, days_advanced=7)

        # 0.20 + 0.04*7 = 0.48, crosses 0.25 once.
        assert snap.active_tropes[0].progress == pytest.approx(0.48, abs=1e-9)
        assert snap.active_tropes[0].beats_fired == 1
        assert snap.days_elapsed == 7

    def test_tick_tropes_emits_trope_time_skip_span(self, otel_capture) -> None:
        """AC-8: ``trope.time_skip`` span fires when days_advanced > 0,
        carrying the TropeTimeSkipFields payload as attributes.
        """
        from sidequest.game.trope_tick import tick_tropes
        from sidequest.telemetry.spans import SPAN_TROPE_TIME_SKIP

        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with(
            [_trope_def(rate_per_day=0.04, thresholds=(0.10, 0.30))]
        )

        tick_tropes(snap, pack, now_turn=11, days_advanced=7)

        spans = otel_capture.get_finished_spans()
        time_skip = [s for s in spans if s.name == SPAN_TROPE_TIME_SKIP]
        assert len(time_skip) == 1, (
            f"expected one trope.time_skip span; got {[s.name for s in spans]}"
        )
        attrs = dict(time_skip[0].attributes or {})
        assert attrs.get("days_requested") == 7
        assert attrs.get("days_applied") == 7
        # Lists serialize as tuples in OTEL attributes; allow either.
        affected = attrs.get("tropes_affected") or ()
        assert "t" in tuple(affected)

    def test_tick_tropes_no_time_skip_span_when_days_zero(
        self, otel_capture
    ) -> None:
        """Zero days = no span — the GM panel relies on span PRESENCE
        as the "a skip happened" signal.
        """
        from sidequest.game.trope_tick import tick_tropes
        from sidequest.telemetry.spans import SPAN_TROPE_TIME_SKIP

        snap = _seed_snapshot([("t", "progressing", 0.0)])
        pack = _pack_with([_trope_def(rate_per_day=0.04)])

        tick_tropes(snap, pack, now_turn=11, days_advanced=0)

        spans = otel_capture.get_finished_spans()
        assert not any(s.name == SPAN_TROPE_TIME_SKIP for s in spans)


# ---------------------------------------------------------------------------
# Pass B interaction (AC-12) — Pass B does not re-fire Pass A2's beats
# ---------------------------------------------------------------------------


class TestPassBInteraction:
    """AC-12: Pass B continues to fire one additional beat if a
    progressing trope has an unfired eligible beat after Pass A2 —
    and Pass B never re-fires what A2 already fired.
    """

    def test_pass_b_does_not_refire_a2_beats(self) -> None:
        """A2 fires the 0.25 beat (progress 0.20 → 0.48). Pass B runs
        and sees beats_fired=1, progress=0.48; the next threshold 0.60
        isn't crossed yet — Pass B fires nothing.
        """
        from sidequest.game.trope_tick import tick_tropes

        snap = _seed_snapshot([("t", "progressing", 0.20)])
        pack = _pack_with(
            [
                _trope_def(
                    rate_per_day=0.04,
                    rate_per_turn=0.0,
                    thresholds=(0.25, 0.60),
                )
            ]
        )

        tick_tropes(snap, pack, now_turn=11, days_advanced=7)

        # Only A2's single beat — Pass B added nothing.
        assert snap.active_tropes[0].beats_fired == 1

    def test_pass_b_still_fires_unfired_eligible_beat_after_a2(self) -> None:
        """Construct a scenario where Pass A2 crosses no thresholds but
        Pass A (rate_per_turn) plus the prior progress lands above an
        unfired beat. Pass B then fires that beat as the standard
        staggered single-beat-per-tick — proving the two passes
        compose, not collide.

        Setup:
        * rate_per_day=0.0 → A2 makes no progress
        * rate_per_turn high enough that Pass A crosses the next beat
        * Pass B sees the post-A progress and fires the staggered beat
        """
        from sidequest.game.trope_tick import tick_tropes

        snap = _seed_snapshot([("t", "progressing", 0.20)])
        pack = _pack_with(
            [
                _trope_def(
                    rate_per_day=0.0,  # A2 does not advance
                    rate_per_turn=0.20,  # A advances; Pass B may fire
                    thresholds=(0.25, 0.60, 0.99),
                )
            ]
        )

        tick_tropes(snap, pack, now_turn=11, days_advanced=7)

        # Pass A applied rate_per_turn * multiplier; Pass B fires a beat
        # that A pushed past 0.25. With days_advanced=7 but zero rate,
        # Pass A2 still emits a span (days > 0) but doesn't advance.
        assert snap.active_tropes[0].beats_fired >= 1
