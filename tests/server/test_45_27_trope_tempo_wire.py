"""Story 45-27 — wire-first boundary tests for trope tempo (cap, cooldown,
foreground/background, per-turn aggregate span).

These tests drive the narrator dispatch path
(``WebSocketSessionHandler._execute_narration_turn``) and assert that:

1. ``tick_tropes`` is called from the dispatch seam exactly once per
   turn — observable by the post-tick mutation of
   ``snapshot.active_tropes`` and by the ``turn.tropes`` aggregate
   span being emitted under the active turn-span context. Calling
   ``tick_tropes`` from a unit test in isolation does NOT satisfy
   this gate; the dispatch path is the seam Felix's playtest exposed.

2. ``_build_turn_context`` populates ``TurnContext.pending_trope_context``
   (Early zone) with the K most-active progressing tropes and
   ``TurnContext.active_trope_summary`` (Valley zone) with the
   remainder. Both fields exist on the dataclass today but are
   *never assigned* in production (grep confirms only the field
   declaration and the orchestrator registration call sites). Wire-
   first means asserting on the TurnContext returned by the actual
   helper, not a render-helper unit test.

3. The ``turn.tropes`` aggregate span fires every turn, including
   silent ones with zero active tropes — silence on the wire would
   look identical to the engine never having run.

4. Cap and cooldown refusals emit their diagnostic spans
   (``trope.cap_blocked``, ``trope.cooldown_blocked``) from the
   dispatch path. Sebastien's lie-detector reads these directly —
   without them the GM panel cannot tell "engine refused" from
   "engine never engaged".

The failure mode this story is closing — Felix's playtest 3 trope
pile-up — was a *wiring* gap, not a logic gap. The data structures and
span constants existed; nothing was calling them. The wire-first
boundary asserts the call site is real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.session import TropeState
from sidequest.telemetry.setup import init_tracer
from tests.server.conftest import _build_turn_context_for_test


@pytest.fixture
def otel_capture():
    """Install an in-memory span exporter on the current TracerProvider."""

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


def _seed_trope(sd, trope_id: str, status: str, progress: float = 0.0) -> None:
    """Pre-seed ``sd.snapshot.active_tropes`` with one entry."""

    sd.snapshot.active_tropes.append(
        TropeState(id=trope_id, status=status, progress=progress, beats_fired=0)
    )


def _quiet_orchestrator() -> AsyncMock:
    """Orchestrator that returns a benign narration without flipping any
    state. Mirrors ``_flipping_orchestrator`` in test_45_20 for the
    "no-op turn" baseline.
    """

    return AsyncMock(
        return_value=NarrationTurnResult(
            narration="Calm settles.",
            is_degraded=False,
            agent_duration_ms=1,
        )
    )


# ---------------------------------------------------------------------------
# AC1 — cap blocks (N+1)th from the dispatch seam
# ---------------------------------------------------------------------------


class TestDispatchSeamCapBlocks:
    """Driving ``_execute_narration_turn`` end-to-end with cap+1 candidate
    tropes must result in exactly cap progressing — proves the tick is
    wired into the dispatch and the cap predicate runs there.
    """

    @pytest.mark.asyncio
    async def test_dispatch_seam_enforces_cap(
        self, session_handler_factory
    ) -> None:
        from sidequest.game.trope_tuning import MAX_SIMULTANEOUS_ACTIVE

        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        # Pre-seed cap+1 tropes whose progress already cleared the
        # activation gate. The genre pack ships exactly 4 trope defs
        # (the_keeper_stirs, extraction_panic, hireling_mutiny,
        # the_deeper_dark) — enough for the cap=3 case.
        cap = MAX_SIMULTANEOUS_ACTIVE
        seed_ids = [
            "the_keeper_stirs",
            "extraction_panic",
            "hireling_mutiny",
            "the_deeper_dark",
        ][: cap + 1]
        for tid in seed_ids:
            _seed_trope(sd, tid, "dormant", 0.30)

        turn_context = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "I look around.", turn_context)

        progressing = [t for t in sd.snapshot.active_tropes if t.status == "progressing"]
        dormant = [t for t in sd.snapshot.active_tropes if t.status == "dormant"]
        assert len(progressing) == cap, (
            f"Cap not enforced via dispatch seam; got {len(progressing)} "
            f"progressing (cap={cap}). statuses="
            f"{[(t.id, t.status) for t in sd.snapshot.active_tropes]}"
        )
        assert len(dormant) == 1, (
            f"Exactly 1 trope must remain queued; got {len(dormant)} "
            f"dormant. statuses={[(t.id, t.status) for t in sd.snapshot.active_tropes]}"
        )

    @pytest.mark.asyncio
    async def test_dispatch_seam_emits_cap_blocked_span(
        self, session_handler_factory, otel_capture
    ) -> None:
        from sidequest.game.trope_tuning import MAX_SIMULTANEOUS_ACTIVE

        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        cap = MAX_SIMULTANEOUS_ACTIVE
        seed_ids = [
            "the_keeper_stirs",
            "extraction_panic",
            "hireling_mutiny",
            "the_deeper_dark",
        ][: cap + 1]
        for tid in seed_ids:
            _seed_trope(sd, tid, "dormant", 0.30)

        otel_capture.clear()
        turn_context = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "I look around.", turn_context)

        cap_blocked = [
            s
            for s in otel_capture.get_finished_spans()
            if s.name == "trope.cap_blocked"
        ]
        assert cap_blocked, (
            "Dispatch seam did not emit trope.cap_blocked — Sebastien's "
            "lie-detector cannot distinguish 'cap engaged' from 'engine "
            "never ran'. Spans: "
            f"{[s.name for s in otel_capture.get_finished_spans()]}"
        )


# ---------------------------------------------------------------------------
# AC2 — cooldown blocks new activation across multiple turns
# ---------------------------------------------------------------------------


class TestDispatchSeamCooldown:
    """Cooldown is multi-turn — proving it requires more than one turn
    through the dispatch seam.
    """

    @pytest.mark.asyncio
    async def test_cooldown_blocks_activation_for_window(
        self, session_handler_factory
    ) -> None:
        from sidequest.game.trope_tuning import FIRE_COOLDOWN_TURNS

        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        # Keeper poised to cross 0.25 threshold on turn 1 → fires →
        # cooldown starts. extraction_panic is a dormant candidate;
        # cooldown blocks for FIRE_COOLDOWN_TURNS turns.
        _seed_trope(sd, "the_keeper_stirs", "progressing", 0.21)
        _seed_trope(sd, "extraction_panic", "dormant", 0.30)

        # Turn 1 — keeper fires.
        turn_context_1 = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "I push deeper.", turn_context_1)

        panic_after_1 = next(
            t for t in sd.snapshot.active_tropes if t.id == "extraction_panic"
        )
        assert panic_after_1.status == "dormant", (
            "Cooldown failed on the same turn the beat fired; "
            f"panic.status={panic_after_1.status!r}"
        )

        # Run further turns within the cooldown window.
        for _ in range(FIRE_COOLDOWN_TURNS):
            sd.orchestrator.run_narration_turn = _quiet_orchestrator()
            ctx = _build_turn_context_for_test(sd)
            await handler._execute_narration_turn(sd, "I wait.", ctx)
            panic = next(
                t for t in sd.snapshot.active_tropes if t.id == "extraction_panic"
            )
            assert panic.status == "dormant", (
                "Cooldown window violated mid-turn; "
                f"panic.status={panic.status!r}"
            )

    @pytest.mark.asyncio
    async def test_cooldown_blocked_span_fires_during_window(
        self, session_handler_factory, otel_capture
    ) -> None:
        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        _seed_trope(sd, "the_keeper_stirs", "progressing", 0.21)
        _seed_trope(sd, "extraction_panic", "dormant", 0.30)

        # Turn 1 — keeper fires, cooldown begins.
        ctx1 = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "I push deeper.", ctx1)

        # Turn 2 — extraction_panic is candidate, cooldown blocks. We
        # only assert on this turn's spans so cap_blocked from turn 1
        # doesn't muddle the count.
        otel_capture.clear()
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()
        ctx2 = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "I wait.", ctx2)

        cooldown_blocked = [
            s
            for s in otel_capture.get_finished_spans()
            if s.name == "trope.cooldown_blocked"
        ]
        assert cooldown_blocked, (
            "trope.cooldown_blocked did not fire from the dispatch seam "
            "during the cooldown window; the GM panel cannot chart "
            "cooldown engagement. Spans: "
            f"{[s.name for s in otel_capture.get_finished_spans()]}"
        )


# ---------------------------------------------------------------------------
# AC4 — foreground/background prompt zone split via _build_turn_context
# ---------------------------------------------------------------------------


class TestBuildTurnContextPopulatesTropeFields:
    """``TurnContext.pending_trope_context`` and ``active_trope_summary``
    are dataclass fields that exist today but are never assigned in
    production. Story 45-27 wires ``_build_turn_context`` to populate
    them from ``snapshot.active_tropes``.
    """

    def test_foreground_field_populated_when_progressing_tropes_present(
        self, session_handler_factory
    ) -> None:
        from sidequest.game.trope_tuning import FOREGROUND_K
        from sidequest.server.session_handler import _build_turn_context

        sd, _ = session_handler_factory(genre="caverns_and_claudes")
        # Seed cap-ish tropes so we have both foreground and background.
        _seed_trope(sd, "the_keeper_stirs", "progressing", 0.80)
        _seed_trope(sd, "extraction_panic", "progressing", 0.50)
        _seed_trope(sd, "hireling_mutiny", "progressing", 0.20)

        ctx = _build_turn_context(sd)

        assert ctx.pending_trope_context is not None, (
            "TurnContext.pending_trope_context is None despite "
            "progressing tropes being present. The Early zone stays "
            "empty and the narrator cannot tell which thread is 'now'."
        )
        # The foreground block must reference the K highest-progress
        # tropes by id. The exact rendering is implementer's choice;
        # the trope ids must be present.
        top_k = ["the_keeper_stirs", "extraction_panic", "hireling_mutiny"][:FOREGROUND_K]
        for tid in top_k:
            assert tid in ctx.pending_trope_context, (
                f"Top-{FOREGROUND_K} trope {tid!r} missing from "
                f"pending_trope_context={ctx.pending_trope_context!r}"
            )

    def test_background_field_populated_with_overflow(
        self, session_handler_factory
    ) -> None:
        from sidequest.game.trope_tuning import FOREGROUND_K, MAX_SIMULTANEOUS_ACTIVE
        from sidequest.server.session_handler import _build_turn_context

        if MAX_SIMULTANEOUS_ACTIVE <= FOREGROUND_K:
            pytest.skip(
                "Test requires cap > FOREGROUND_K so an overflow exists "
                "for the Valley summary."
            )

        sd, _ = session_handler_factory(genre="caverns_and_claudes")
        _seed_trope(sd, "the_keeper_stirs", "progressing", 0.80)
        _seed_trope(sd, "extraction_panic", "progressing", 0.50)
        _seed_trope(sd, "hireling_mutiny", "progressing", 0.20)

        ctx = _build_turn_context(sd)

        # The background field must reference the lowest-progress
        # progressing trope (it's the overflow past FOREGROUND_K).
        assert ctx.active_trope_summary is not None, (
            "active_trope_summary missing despite a Valley-zone overflow."
        )
        assert "hireling_mutiny" in ctx.active_trope_summary, (
            f"Lowest-progress progressing trope missing from "
            f"active_trope_summary={ctx.active_trope_summary!r}"
        )

    def test_both_fields_none_when_no_progressing_tropes(
        self, session_handler_factory
    ) -> None:
        """Zero-byte-leak: the orchestrator's prompt-section registry
        skips registration when the field is None — so the Early /
        Valley sections never appear in a prompt for a world without
        progressing tropes. Empty strings would still register the
        section header and waste prompt budget.
        """

        from sidequest.server.session_handler import _build_turn_context

        sd, _ = session_handler_factory(genre="caverns_and_claudes")
        # Seed a dormant + a resolved — neither should reach the prompt.
        _seed_trope(sd, "the_keeper_stirs", "dormant", 0.05)
        _seed_trope(sd, "extraction_panic", "resolved", 1.00)

        ctx = _build_turn_context(sd)

        assert ctx.pending_trope_context is None, (
            f"pending_trope_context must be None with zero progressing "
            f"tropes; got {ctx.pending_trope_context!r}"
        )
        assert ctx.active_trope_summary is None, (
            f"active_trope_summary must be None with zero progressing "
            f"tropes; got {ctx.active_trope_summary!r}"
        )


# ---------------------------------------------------------------------------
# AC5 — turn.tropes aggregate span fires every turn including silent
# ---------------------------------------------------------------------------


class TestTurnTropesAggregateSpan:
    """``turn.tropes`` is the per-turn aggregate the GM panel reads to
    chart tempo. Sebastien's lie-detector requires three attributes
    (active_trope_count, progression_max, progression_avg) plus the
    queued-count diagnostic and the cooldown indicator. Without the
    span every turn — including silent ones — the panel chart goes
    dark instead of showing "0 active tropes" as a real signal.
    """

    @pytest.mark.asyncio
    async def test_span_fires_on_every_turn(
        self, session_handler_factory, otel_capture
    ) -> None:
        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        _seed_trope(sd, "the_keeper_stirs", "progressing", 0.30)

        # Three turns; assert one turn.tropes span per turn.
        turn_count = 3
        for _ in range(turn_count):
            sd.orchestrator.run_narration_turn = _quiet_orchestrator()
            ctx = _build_turn_context_for_test(sd)
            await handler._execute_narration_turn(sd, "Continue.", ctx)

        turn_tropes = [
            s for s in otel_capture.get_finished_spans() if s.name == "turn.tropes"
        ]
        assert len(turn_tropes) == turn_count, (
            f"Expected {turn_count} turn.tropes spans (one per turn); "
            f"got {len(turn_tropes)}. The GM panel chart needs a sample "
            "per turn to render tempo continuously."
        )

    @pytest.mark.asyncio
    async def test_span_fires_even_when_no_active_tropes(
        self, session_handler_factory, otel_capture
    ) -> None:
        """Silent turn — no progressing tropes — must still emit the
        aggregate span with active_trope_count=0. Without it the
        GM panel chart drops to "no data" and looks identical to
        the engine being broken.
        """

        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()
        # snapshot.active_tropes left empty.

        otel_capture.clear()
        ctx = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "I wait.", ctx)

        turn_tropes = [
            s for s in otel_capture.get_finished_spans() if s.name == "turn.tropes"
        ]
        assert len(turn_tropes) == 1, (
            "Silent turn must emit exactly one turn.tropes span; got "
            f"{len(turn_tropes)}. The 'no active tropes' state is itself "
            "a tempo signal — silence on the wire would lie."
        )
        attrs = dict(turn_tropes[0].attributes or {})
        assert attrs.get("active_trope_count") == 0, (
            f"active_trope_count must be 0 on a silent turn; "
            f"attrs.active_trope_count={attrs.get('active_trope_count')}"
        )

    @pytest.mark.asyncio
    async def test_span_carries_required_three_metrics(
        self, session_handler_factory, otel_capture
    ) -> None:
        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        _seed_trope(sd, "the_keeper_stirs", "progressing", 0.30)
        _seed_trope(sd, "extraction_panic", "progressing", 0.60)

        otel_capture.clear()
        ctx = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "Continue.", ctx)

        turn_tropes = [
            s for s in otel_capture.get_finished_spans() if s.name == "turn.tropes"
        ]
        assert turn_tropes, "turn.tropes span did not fire"
        attrs = dict(turn_tropes[0].attributes or {})

        # The three metrics named in the story description (the GM
        # panel reads these directly).
        assert isinstance(attrs.get("active_trope_count"), int), (
            f"active_trope_count missing or wrong type; attrs={attrs}"
        )
        assert attrs.get("active_trope_count") == 2

        progression_max = attrs.get("progression_max")
        assert isinstance(progression_max, float), (
            f"progression_max missing or wrong type; got {progression_max!r}"
        )
        # Bounded in [0, 1]. The keeper started at 0.30 and got ticked
        # by the engine — exact value depends on the rate multiplier
        # but it stays bounded.
        assert 0.0 <= progression_max <= 1.0

        progression_avg = attrs.get("progression_avg")
        assert isinstance(progression_avg, float)
        assert 0.0 <= progression_avg <= 1.0
        assert progression_avg <= progression_max, (
            "average must be ≤ max by definition; "
            f"avg={progression_avg}, max={progression_max}"
        )

    @pytest.mark.asyncio
    async def test_span_carries_diagnostic_attributes(
        self, session_handler_factory, otel_capture
    ) -> None:
        """Beyond the three story-named metrics, the span carries
        ``queued_count`` and ``cooldown_active`` so the GM panel can
        surface why a tempo dip is happening — refusal, not absence.
        """

        from sidequest.game.trope_tuning import MAX_SIMULTANEOUS_ACTIVE

        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        cap = MAX_SIMULTANEOUS_ACTIVE
        seed_ids = [
            "the_keeper_stirs",
            "extraction_panic",
            "hireling_mutiny",
            "the_deeper_dark",
        ][: cap + 1]
        for tid in seed_ids:
            _seed_trope(sd, tid, "dormant", 0.30)

        otel_capture.clear()
        ctx = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "Continue.", ctx)

        turn_tropes = [
            s for s in otel_capture.get_finished_spans() if s.name == "turn.tropes"
        ]
        assert turn_tropes, "turn.tropes span did not fire"
        attrs = dict(turn_tropes[0].attributes or {})

        assert attrs.get("queued_count") == 1, (
            f"queued_count must equal the number of dormant tropes the "
            f"cap held back this turn; attrs.queued_count="
            f"{attrs.get('queued_count')}"
        )
        # cooldown_active is a bool. False on a turn where nothing
        # fired (this turn — dormant→progressing only).
        assert attrs.get("cooldown_active") is False, (
            f"cooldown_active must be False when no beat fired; "
            f"attrs.cooldown_active={attrs.get('cooldown_active')!r}"
        )

    @pytest.mark.asyncio
    async def test_span_fires_under_root_turn_span(
        self, session_handler_factory, otel_capture
    ) -> None:
        """The aggregate span must be a child of the root ``turn`` span.
        This is the wire-first proof that the tick is invoked inside
        ``_execute_narration_turn`` and not before/after it (where the
        watcher's per-turn correlation would break).
        """

        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        otel_capture.clear()
        ctx = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "I wait.", ctx)

        finished = otel_capture.get_finished_spans()
        turn_tropes = [s for s in finished if s.name == "turn.tropes"]
        turn_root = [s for s in finished if s.name == "turn"]
        assert turn_tropes, "turn.tropes span missing"
        assert turn_root, "root turn span missing — fixture wiring broke"
        # parent must be the root turn span.
        parent_id = turn_tropes[0].parent.span_id if turn_tropes[0].parent else None
        assert parent_id == turn_root[0].context.span_id, (
            "turn.tropes is not nested under the root turn span — the "
            "tick is being called outside the turn-span context, "
            "breaking the watcher's per-turn correlation."
        )


# ---------------------------------------------------------------------------
# Tick happens once per turn (not zero, not multiple)
# ---------------------------------------------------------------------------


class TestTickIsCalledOncePerTurn:
    """Defensive — it would be possible for the wire to be over-eager
    (calling tick multiple times per turn — pile-up returns) or
    under-eager (skipping turns — telemetry holes). One tick per turn
    is the contract.
    """

    @pytest.mark.asyncio
    async def test_progress_advances_exactly_once_per_dispatch_call(
        self, session_handler_factory
    ) -> None:
        from sidequest.game.trope_tuning import PROGRESSION_RATE_MULTIPLIER

        sd, handler = session_handler_factory(genre="caverns_and_claudes")
        sd.orchestrator.run_narration_turn = _quiet_orchestrator()

        # Use a content trope whose YAML rate is known
        # (the_keeper_stirs: rate_per_turn=0.02 in the genre pack).
        _seed_trope(sd, "the_keeper_stirs", "progressing", 0.10)

        ctx = _build_turn_context_for_test(sd)
        await handler._execute_narration_turn(sd, "Continue.", ctx)

        keeper = next(
            t for t in sd.snapshot.active_tropes if t.id == "the_keeper_stirs"
        )
        # the_keeper_stirs rate is 0.02 in the pack; multiplied by the
        # tuning brake it is 0.02 * PROGRESSION_RATE_MULTIPLIER.
        expected = 0.10 + 0.02 * PROGRESSION_RATE_MULTIPLIER
        assert keeper.progress == pytest.approx(expected, abs=1e-6), (
            f"progress={keeper.progress}, expected≈{expected}. Either "
            "the tick fired more than once (double-advance) or did not "
            "fire at all (no advance)."
        )
