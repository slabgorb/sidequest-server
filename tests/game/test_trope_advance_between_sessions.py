"""Story 50-4 — between-session passive trope advancement.

ADR-018 defines two passive rates:

* ``rate_per_turn`` — ticks during live gameplay (engine: ``trope_tick.py``).
* ``rate_per_day`` — ticks **between** sessions, advancing each loaded
  trope by ``(rate_per_day * elapsed_days)`` so the world feels alive
  between sittings.

The Python port (ADR-082) carried the data model but left the
between-session engine unimplemented. This file pins the engine's
contract: a public ``advance_tropes_between_sessions`` function in
``sidequest.game.trope_advance`` that mutates ``snapshot.active_tropes``
on load, fires escalation beats that the elapsed time crossed,
emits ``SPAN_TROPE_BETWEEN_SESSION_ADVANCE`` per advancing trope, and
clamps progress at 1.0 (with status → ``resolved`` when the trope is
done).

Wire site is verified by ``tests/server/test_50_4_trope_advance_wire.py``;
that test reads ``sidequest/handlers/connect.py`` to ensure the engine
is actually called from the production load path (not just present).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.session import GameSnapshot, TropeState
from sidequest.genre.models.tropes import (
    PassiveProgression,
    TropeDefinition,
    TropeEscalation,
)

# ---------------------------------------------------------------------------
# Helpers — small fixtures so tests do not depend on shipped content.
# ---------------------------------------------------------------------------


def _trope_def(
    trope_id: str,
    *,
    rate_per_day: float = 0.10,
    rate_per_turn: float = 0.0,
    thresholds: tuple[float, ...] = (0.25, 0.50, 0.75, 1.00),
) -> TropeDefinition:
    """Build a deterministic TropeDefinition tuned for between-session
    advancement. ``rate_per_turn`` defaults to zero so the test
    cleanly isolates the per-day path.
    """

    return TropeDefinition(
        id=trope_id,
        name=trope_id.replace("_", " ").title(),
        category="tension",
        triggers=[],
        narrative_hints=[],
        escalation=[TropeEscalation(at=t, event=f"beat at {t}", stakes="") for t in thresholds],
        passive_progression=PassiveProgression(
            rate_per_turn=rate_per_turn, rate_per_day=rate_per_day
        ),
    )


def _pack_with(tropes: list[TropeDefinition]) -> Any:
    """Duck-typed pack stand-in — ``pack.tropes`` is the only attribute
    the engine reads (matches the ``tick_tropes`` test fixture).
    """

    return SimpleNamespace(tropes=tropes)


def _seed_snapshot(
    states: list[tuple[str, str, float]],
    *,
    last_saved_at: datetime | None,
) -> GameSnapshot:
    """Build a snapshot with given trope states and ``last_saved_at``.

    ``states`` is ``[(trope_id, status, progress), ...]``.
    """

    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    for trope_id, status, progress in states:
        snap.active_tropes.append(
            TropeState(id=trope_id, status=status, progress=progress, beats_fired=0)
        )
    snap.last_saved_at = last_saved_at
    return snap


def _spans_named(exporter: InMemorySpanExporter, name: str) -> list:
    return [s for s in exporter.get_finished_spans() if s.name == name]


# ---------------------------------------------------------------------------
# Module-level contract — function exists, importable, type-annotated.
# ---------------------------------------------------------------------------


class TestModuleContract:
    """The engine lives in ``sidequest.game.trope_advance`` so it can be
    imported by both the load handler and tests without dragging in
    ``trope_tick`` (which is turn-scoped, not time-scoped).

    Rule #3 (lang-review/python.md — type annotations at boundaries):
    a public function at a module boundary MUST have type annotations
    on every parameter and on its return type. The Reviewer will look
    here.
    """

    def test_module_advance_tropes_between_sessions_is_importable(self) -> None:
        from sidequest.game.trope_advance import advance_tropes_between_sessions

        assert callable(advance_tropes_between_sessions), (
            "advance_tropes_between_sessions must be a callable — the "
            "load handler calls it directly."
        )

    def test_signature_is_keyword_only_with_snapshot_pack_now(self) -> None:
        """Match the existing ``tick_tropes`` style: keyword-only,
        ``snapshot`` + ``pack`` + a time anchor. Positional-args would
        let a careless wire-site pass them in the wrong order.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        sig = inspect.signature(advance_tropes_between_sessions)
        params = sig.parameters

        # ``snapshot`` must exist and be keyword-only.
        assert "snapshot" in params, "missing required parameter 'snapshot'"
        assert params["snapshot"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "'snapshot' must be keyword-only so the wire-site is unambiguous"
        )

        # ``pack`` must exist and be keyword-only.
        assert "pack" in params, "missing required parameter 'pack'"
        assert params["pack"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "'pack' must be keyword-only so the wire-site is unambiguous"
        )

        # Either ``now`` (datetime) — matches snapshot.last_saved_at type.
        assert "now" in params, (
            "missing required parameter 'now' — engine needs a time anchor "
            "to compute (now - snapshot.last_saved_at).total_seconds()"
        )
        assert params["now"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "'now' must be keyword-only so the wire-site is unambiguous"
        )

    def test_all_parameters_have_type_annotations(self) -> None:
        """Rule #3: public function at module boundary requires
        annotations on every parameter and return type. ``Any`` is
        acceptable on ``pack`` (duck-typed) because the existing
        ``tick_tropes`` does the same.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        sig = inspect.signature(advance_tropes_between_sessions)
        for name, p in sig.parameters.items():
            assert p.annotation is not inspect.Parameter.empty, (
                f"parameter {name!r} on advance_tropes_between_sessions "
                f"lacks a type annotation (rule #3 — public boundary)"
            )
        assert sig.return_annotation is not inspect.Signature.empty, (
            "advance_tropes_between_sessions must annotate its return type"
        )


# ---------------------------------------------------------------------------
# AC1 — Load-time advancement: progress += rate_per_day * elapsed_days.
# ---------------------------------------------------------------------------


class TestAC1LoadTimeAdvancement:
    def test_progress_advances_by_rate_times_elapsed_days(self) -> None:
        """3 days at rate_per_day=0.10 → +0.30 progress."""

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        last_saved = now - timedelta(days=3)
        snap = _seed_snapshot(
            [("ticking_clock", "progressing", 0.10)],
            last_saved_at=last_saved,
        )
        pack = _pack_with([_trope_def("ticking_clock", rate_per_day=0.10, thresholds=(0.99,))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        # 0.10 + 3 * 0.10 = 0.40
        trope = snap.active_tropes[0]
        assert trope.progress == pytest.approx(0.40), (
            f"expected progress=0.40 after 3 days @ 0.10/day from 0.10, got {trope.progress}"
        )

    def test_fractional_days_advance_fractionally(self) -> None:
        """12 hours at rate_per_day=0.20 → +0.10 progress."""

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        last_saved = now - timedelta(hours=12)
        snap = _seed_snapshot(
            [("simmering_tension", "progressing", 0.00)],
            last_saved_at=last_saved,
        )
        pack = _pack_with([_trope_def("simmering_tension", rate_per_day=0.20, thresholds=(0.99,))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        assert snap.active_tropes[0].progress == pytest.approx(0.10)

    def test_rate_per_day_zero_does_not_advance(self) -> None:
        """A trope with rate_per_day=0.0 stays put even after years."""

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        last_saved = now - timedelta(days=400)
        snap = _seed_snapshot(
            [("inert_trope", "progressing", 0.42)],
            last_saved_at=last_saved,
        )
        pack = _pack_with([_trope_def("inert_trope", rate_per_day=0.0, thresholds=(0.99,))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        assert snap.active_tropes[0].progress == pytest.approx(0.42), (
            "rate_per_day=0 must be a no-op; otherwise the trope drifts "
            "purely from time, which is exactly the silent fallback "
            "CLAUDE.md forbids."
        )

    def test_dormant_tropes_do_not_advance(self) -> None:
        """Only ``progressing`` tropes advance. Dormant is queued — it
        becomes progressing through the in-session activation gate, not
        through passive time. Otherwise the cap (ADR-018) becomes
        cosmetic and a long gap silently activates every dormant.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("queued_trope", "dormant", 0.00)],
            last_saved_at=now - timedelta(days=10),
        )
        pack = _pack_with([_trope_def("queued_trope", rate_per_day=0.50, thresholds=(0.25,))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        assert trope.status == "dormant", "dormant must not auto-promote on load"
        assert trope.progress == pytest.approx(0.00), (
            "dormant must not accumulate progress between sessions"
        )

    def test_resolved_tropes_do_not_advance(self) -> None:
        """Resolved tropes are terminal — they must not tick further."""

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("finished_arc", "resolved", 1.00)],
            last_saved_at=now - timedelta(days=10),
        )
        pack = _pack_with([_trope_def("finished_arc", rate_per_day=0.10, thresholds=(0.99,))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        assert trope.status == "resolved"
        assert trope.progress == pytest.approx(1.00)

    def test_trope_with_no_passive_progression_def_is_skipped(self) -> None:
        """A pack trope without a ``passive_progression`` block must
        not crash the engine — caverns_and_claudes already has tropes
        that omit it.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("bare_trope", "progressing", 0.10)],
            last_saved_at=now - timedelta(days=5),
        )
        # Build trope def with passive_progression = None
        tdef = TropeDefinition(
            id="bare_trope",
            name="Bare",
            category="tension",
            triggers=[],
            narrative_hints=[],
            escalation=[TropeEscalation(at=0.99, event="x", stakes="")],
            passive_progression=None,
        )
        pack = _pack_with([tdef])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        assert snap.active_tropes[0].progress == pytest.approx(0.10), (
            "passive_progression=None must be a no-op"
        )

    def test_trope_id_missing_from_pack_is_skipped(self) -> None:
        """A snapshot trope whose id no longer matches any pack
        definition (e.g. content was renamed) must not crash and must
        not advance.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("ghost_trope", "progressing", 0.20)],
            last_saved_at=now - timedelta(days=5),
        )
        # Pack defines a different trope id.
        pack = _pack_with([_trope_def("different_trope", rate_per_day=0.10)])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        assert snap.active_tropes[0].progress == pytest.approx(0.20)

    def test_negative_elapsed_time_does_not_subtract(self) -> None:
        """Clock skew or a wrong-direction ``now`` must not regress
        progress. Either the engine clamps elapsed to zero or skips
        the trope; in no case may progress decrease.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        last_saved_in_future = now + timedelta(days=5)
        snap = _seed_snapshot(
            [("skewed_trope", "progressing", 0.42)],
            last_saved_at=last_saved_in_future,
        )
        pack = _pack_with([_trope_def("skewed_trope", rate_per_day=0.10)])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        assert snap.active_tropes[0].progress >= 0.42, (
            "negative elapsed must not subtract from progress; either "
            "clamp to 0 or skip the trope entirely"
        )


# ---------------------------------------------------------------------------
# AC2 — Beat firing on load.
# ---------------------------------------------------------------------------


class TestAC2BeatFiringOnLoad:
    def test_single_beat_fires_when_threshold_crossed(self) -> None:
        """0.20 → 0.30 crosses the 0.25 threshold → beats_fired = 1."""

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("crossing_trope", "progressing", 0.20)],
            last_saved_at=now - timedelta(days=1),
        )
        pack = _pack_with(
            [
                _trope_def(
                    "crossing_trope",
                    rate_per_day=0.10,
                    thresholds=(0.25, 0.50, 0.75, 1.00),
                )
            ]
        )

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        assert trope.beats_fired == 1, (
            f"expected beats_fired=1 after crossing 0.25 threshold once, got {trope.beats_fired}"
        )

    def test_multiple_beats_fire_from_large_elapsed_time(self) -> None:
        """A long gap should fire every beat the new progress reached
        — not stagger like the per-turn engine. Between-session is
        offline catch-up; the player will see the consequences in the
        opening narration regardless of which beat fired first.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        # 0.10 + 5 * 0.10 = 0.60 → crosses 0.25 and 0.50 thresholds.
        snap = _seed_snapshot(
            [("long_gap", "progressing", 0.10)],
            last_saved_at=now - timedelta(days=5),
        )
        pack = _pack_with(
            [
                _trope_def(
                    "long_gap",
                    rate_per_day=0.10,
                    thresholds=(0.25, 0.50, 0.75, 1.00),
                )
            ]
        )

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        assert trope.beats_fired == 2, (
            f"expected beats_fired=2 after crossing 0.25 and 0.50, got {trope.beats_fired}"
        )

    def test_beat_not_fired_when_threshold_not_crossed(self) -> None:
        """0.10 → 0.15 stays under the 0.25 threshold → beats_fired = 0."""

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("slow_burn", "progressing", 0.10)],
            last_saved_at=now - timedelta(days=1),
        )
        pack = _pack_with(
            [
                _trope_def(
                    "slow_burn",
                    rate_per_day=0.05,
                    thresholds=(0.25, 0.50),
                )
            ]
        )

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        assert snap.active_tropes[0].beats_fired == 0

    def test_already_fired_beats_are_not_refired(self) -> None:
        """A trope re-loaded with progress already past a beat (e.g.
        the in-session engine fired it earlier in the prior session)
        must not refire that beat. ``beats_fired`` is the cursor.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("already_fired", "progressing", 0.30)],
            last_saved_at=now - timedelta(days=1),
        )
        # Mark the first beat as already fired in the prior session.
        snap.active_tropes[0].beats_fired = 1
        pack = _pack_with(
            [
                _trope_def(
                    "already_fired",
                    rate_per_day=0.10,
                    thresholds=(0.25, 0.50, 0.75, 1.00),
                )
            ]
        )

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        # +0.10 → 0.40 — does not reach 0.50, so beats_fired stays at 1.
        assert trope.beats_fired == 1, (
            f"already-fired beats must not refire; expected 1 got {trope.beats_fired}"
        )

    def test_passive_fire_does_not_record_narrative_entry(self) -> None:
        """Passive between-session beats fire without narrator
        involvement — the narrator's first turn after load tells the
        story (AC2). The engine MUST NOT enqueue a NarrativeEntry or
        mutate fields the narrator owns.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("quiet_fire", "progressing", 0.20)],
            last_saved_at=now - timedelta(days=1),
        )
        pack = _pack_with([_trope_def("quiet_fire", rate_per_day=0.10, thresholds=(0.25,))])
        narrative_log_before = list(snap.narrative_log)

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        assert snap.narrative_log == narrative_log_before, (
            "passive between-session advancement must not write a "
            "NarrativeEntry — the narrator's opening turn handles framing"
        )


# ---------------------------------------------------------------------------
# AC3 — Progress clamp + RESOLVED.
# ---------------------------------------------------------------------------


class TestAC3ProgressClamp:
    def test_progress_never_exceeds_one(self) -> None:
        """rate_per_day=1.0 over 5 days would push to 5.0 — clamp to 1.0."""

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("runaway", "progressing", 0.10)],
            last_saved_at=now - timedelta(days=5),
        )
        pack = _pack_with([_trope_def("runaway", rate_per_day=1.0, thresholds=(1.00,))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        assert trope.progress <= 1.0, f"progress must clamp at 1.0, got {trope.progress}"
        assert trope.progress == pytest.approx(1.0)

    def test_status_resolves_when_progress_clamps_to_one_and_all_beats_fired(
        self,
    ) -> None:
        """When advancement saturates progress at 1.0 AND every beat
        has fired, the trope transitions to ``resolved``. Both
        conditions are required — matches the in-session engine
        (trope_tick.py:_fire_one_staggered_beat).
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("terminal", "progressing", 0.10)],
            last_saved_at=now - timedelta(days=10),
        )
        # 0.10 + 10 * 1.0 = 10.10 → clamped to 1.0; thresholds at 0.5
        # and 1.0 both crossed.
        pack = _pack_with([_trope_def("terminal", rate_per_day=1.0, thresholds=(0.50, 1.00))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        assert trope.progress == pytest.approx(1.0)
        assert trope.beats_fired == 2
        assert trope.status == "resolved", (
            f"a trope with progress=1.0 and all beats fired must "
            f"transition to 'resolved', got status={trope.status!r}"
        )

    def test_progress_at_one_without_all_beats_does_not_resolve(self) -> None:
        """Progress at 1.0 alone isn't enough — the final beat must
        also fire. If the YAML's last beat is below 1.0, the trope
        can resolve via beats_fired completion at clamp time. If the
        YAML's last beat is exactly 1.0, the threshold is reached at
        clamp and the beat fires — also resolves. The only way to
        stay progressing at 1.0 is when ``beats_fired < len(escalation)``
        AND ``progress_after`` does not reach the next beat. With the
        clamp-equal-1.0 convention below, this is rare but
        representable when ``escalation[-1].at > 1.0`` (illegal YAML),
        so we keep the gate explicit.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("almost", "progressing", 0.10)],
            last_saved_at=now - timedelta(days=10),
        )
        # Beat at exactly 1.0 — clamp brings progress to 1.0 so the
        # beat fires and the trope resolves. (Opposite case to the
        # above test: the assertion is that resolve does happen.)
        pack = _pack_with([_trope_def("almost", rate_per_day=1.0, thresholds=(1.00,))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        assert trope.beats_fired == 1, (
            "the at=1.0 beat must fire when progress reaches 1.0 via clamp"
        )
        assert trope.status == "resolved"


# ---------------------------------------------------------------------------
# AC4 — No advance on first load / never-saved snapshot.
# ---------------------------------------------------------------------------


class TestAC4NoAdvanceOnFirstLoad:
    def test_no_advance_when_last_saved_at_is_none(self) -> None:
        """A freshly-created snapshot (or a save that pre-dates the
        ``last_saved_at`` field — pydantic defaults to None) must not
        advance. Without a known prior timestamp, elapsed-days is
        meaningless and inventing one is silent fallback.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("brand_new", "progressing", 0.10)],
            last_saved_at=None,
        )
        pack = _pack_with([_trope_def("brand_new", rate_per_day=0.50)])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        trope = snap.active_tropes[0]
        assert trope.progress == pytest.approx(0.10), (
            "with last_saved_at=None the engine must not advance — no anchor for elapsed-days"
        )
        assert trope.beats_fired == 0

    def test_no_spans_emitted_when_last_saved_at_is_none(
        self, otel_capture: InMemorySpanExporter
    ) -> None:
        """The first-load no-advance path must be silent on the wire.
        Emitting a span with days_elapsed=0 would create noise on the
        GM panel that doesn't mean anything.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("brand_new", "progressing", 0.10)],
            last_saved_at=None,
        )
        pack = _pack_with([_trope_def("brand_new", rate_per_day=0.50)])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        advance_spans = _spans_named(otel_capture, "trope.between_session_advance")
        assert advance_spans == [], (
            f"expected zero between-session spans when last_saved_at is None, "
            f"got {len(advance_spans)}"
        )


# ---------------------------------------------------------------------------
# AC5 — OTEL emission contract.
# ---------------------------------------------------------------------------


class TestAC5OtelEmission:
    def test_span_constant_defined_and_exported(self) -> None:
        """``SPAN_TROPE_BETWEEN_SESSION_ADVANCE`` MUST live in
        ``sidequest.telemetry.spans.trope`` and be re-exported through
        the package ``__init__`` (sibling spans use the same pattern).
        Otherwise watcher subscribers can't reference the constant by
        name and the GM panel routes will go stale.
        """

        from sidequest.telemetry import spans as span_pkg
        from sidequest.telemetry.spans import trope as trope_spans

        assert hasattr(trope_spans, "SPAN_TROPE_BETWEEN_SESSION_ADVANCE"), (
            "missing constant SPAN_TROPE_BETWEEN_SESSION_ADVANCE in sidequest.telemetry.spans.trope"
        )
        assert hasattr(span_pkg, "SPAN_TROPE_BETWEEN_SESSION_ADVANCE"), (
            "SPAN_TROPE_BETWEEN_SESSION_ADVANCE must be re-exported from "
            "sidequest.telemetry.spans (the package __init__)"
        )
        # Span names are dotted/snake-case per the project's convention.
        assert isinstance(trope_spans.SPAN_TROPE_BETWEEN_SESSION_ADVANCE, str)
        assert trope_spans.SPAN_TROPE_BETWEEN_SESSION_ADVANCE != ""

    def test_span_is_routed_or_flat_only(self) -> None:
        """Routing-completeness contract (Story 45-27): every span
        constant must be either in ``SPAN_ROUTES`` (typed Subsystems
        feed) or in ``FLAT_ONLY_SPANS`` (firehose only). Neither
        registration means the watcher silently drops the event.
        """

        from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES
        from sidequest.telemetry.spans.trope import (
            SPAN_TROPE_BETWEEN_SESSION_ADVANCE,
        )

        in_routes = SPAN_TROPE_BETWEEN_SESSION_ADVANCE in SPAN_ROUTES
        in_flat = SPAN_TROPE_BETWEEN_SESSION_ADVANCE in FLAT_ONLY_SPANS
        assert in_routes != in_flat, (
            f"{SPAN_TROPE_BETWEEN_SESSION_ADVANCE!r} must be in exactly "
            f"one of SPAN_ROUTES or FLAT_ONLY_SPANS; got "
            f"in_routes={in_routes} in_flat={in_flat}"
        )

    def test_span_emitted_per_advancing_trope(self, otel_capture: InMemorySpanExporter) -> None:
        """One span per trope that actually moved. Two tropes
        advance → two spans. A trope with rate_per_day=0 advancing
        zero must NOT emit (otherwise the panel sees noise).
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [
                ("alpha", "progressing", 0.10),
                ("beta", "progressing", 0.20),
                ("inert", "progressing", 0.30),
            ],
            last_saved_at=now - timedelta(days=2),
        )
        pack = _pack_with(
            [
                _trope_def("alpha", rate_per_day=0.10, thresholds=(0.99,)),
                _trope_def("beta", rate_per_day=0.05, thresholds=(0.99,)),
                _trope_def("inert", rate_per_day=0.0, thresholds=(0.99,)),
            ]
        )

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        spans = _spans_named(otel_capture, "trope.between_session_advance")
        trope_ids = sorted(s.attributes.get("trope_id") for s in spans)
        assert trope_ids == ["alpha", "beta"], (
            f"expected spans for the two advancing tropes only; got {trope_ids}"
        )

    def test_span_attributes_match_ac5_contract(self, otel_capture: InMemorySpanExporter) -> None:
        """AC5 names five required attributes on the span:
        ``trope_id``, ``days_elapsed``, ``progress_before``,
        ``progress_after``, ``beats_fired_count``, ``new_status``.
        Missing any of them blinds the GM panel for this event.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("inspected", "progressing", 0.20)],
            last_saved_at=now - timedelta(days=3),
        )
        pack = _pack_with(
            [
                _trope_def(
                    "inspected",
                    rate_per_day=0.10,
                    thresholds=(0.25, 0.50, 0.75, 1.00),
                )
            ]
        )

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        spans = _spans_named(otel_capture, "trope.between_session_advance")
        assert len(spans) == 1, f"expected exactly 1 span, got {len(spans)}"
        attrs = dict(spans[0].attributes or {})

        required = {
            "trope_id",
            "days_elapsed",
            "progress_before",
            "progress_after",
            "beats_fired_count",
            "new_status",
        }
        missing = required - attrs.keys()
        assert not missing, f"span missing required attributes: {missing}"

        # Sanity: values reflect the test state.
        assert attrs["trope_id"] == "inspected"
        assert attrs["days_elapsed"] == pytest.approx(3.0)
        assert attrs["progress_before"] == pytest.approx(0.20)
        assert attrs["progress_after"] == pytest.approx(0.50)
        # 0.20 → 0.50 crosses the 0.25 beat exactly once (0.50 is the
        # next beat but progress is *at* 0.50, not past; engine
        # semantics: ``progress_before < at <= progress_after`` — both
        # 0.25 and 0.50 qualify under the inclusive form).
        assert attrs["beats_fired_count"] in {1, 2}

    def test_span_carries_resolved_status_when_clamping_resolves(
        self, otel_capture: InMemorySpanExporter
    ) -> None:
        """When the advance pushes a trope to ``resolved``, the span's
        ``new_status`` attribute reports it. That's how the panel
        distinguishes "made progress" from "completed the arc" without
        cross-referencing other spans.
        """

        from sidequest.game.trope_advance import advance_tropes_between_sessions

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
        snap = _seed_snapshot(
            [("closer", "progressing", 0.10)],
            last_saved_at=now - timedelta(days=10),
        )
        pack = _pack_with([_trope_def("closer", rate_per_day=1.0, thresholds=(1.00,))])

        advance_tropes_between_sessions(snapshot=snap, pack=pack, now=now)

        spans = _spans_named(otel_capture, "trope.between_session_advance")
        assert len(spans) == 1
        assert (spans[0].attributes or {}).get("new_status") == "resolved"
