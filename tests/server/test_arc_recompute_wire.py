"""Story 45-19 — wire-first boundary tests for the arc-recompute tick.

These tests drive the narrator dispatch path
(``_execute_narration_turn``) and assert that the arc-recompute fires
from the actual seam — *not* from an isolated unit test on
``materialize_world``. Per the wire-first workflow the test must hit
the outermost reachable layer; here that is the post-
``record_interaction()`` site inside the narration turn so the OTEL
panel can see arc ticks alongside every other turn-scoped span.

Felix's Playtest 3 (2026-04-19, evropi session) reached turn 72 with no
arc tick ever firing past the chargen materialization. The bug was a
missing call site, not a missing helper — so the test that catches it
must exercise the call site, which is what these tests do.
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
from sidequest.game.history_chapter import HistoryChapter
from sidequest.game.world_materialization import (
    ARC_RECOMPUTE_INTERVAL,
    CampaignMaturity,
)
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


def _three_tier_chapters() -> list[HistoryChapter]:
    """Synthetic chapter list used by the wire tests.

    The fixture pack only ships a single world; constructing the
    chapters directly avoids a dependency on the on-disk ``history.yaml``
    layout and keeps the test focused on the dispatch seam.
    """

    return [
        HistoryChapter(id="early", label="Early arc"),
        HistoryChapter(id="mid", label="Mid arc"),
        HistoryChapter(id="veteran", label="Veteran arc"),
    ]


# ---------------------------------------------------------------------------
# _SessionData wiring — cached chapters land at chargen, not on every tick.
# ---------------------------------------------------------------------------


class TestSessionDataCachesHistoryChapters:
    """Story 45-19 — the parsed chapter list must live on
    ``_SessionData`` so the dispatch loop is not re-parsing
    ``history.yaml`` per turn.

    These guard the field shape; the chargen-time population path is
    covered by the integration test that drives a full chargen
    confirmation.
    """

    def test_session_data_has_cached_history_chapters_field(self, session_fixture) -> None:
        sd, _handler = session_fixture
        assert hasattr(sd, "cached_history_chapters"), (
            "_SessionData must expose cached_history_chapters so the "
            "dispatch loop can recompute world_history without re-parsing "
            "history.yaml every turn."
        )

    def test_cached_history_chapters_default_is_empty_list(self, session_fixture) -> None:
        sd, _handler = session_fixture
        assert sd.cached_history_chapters == [], (
            "cached_history_chapters must default to an empty list so a "
            "session whose pack has no history.yaml still constructs cleanly."
        )

    def test_cached_history_chapters_accepts_history_chapter_list(self, session_fixture) -> None:
        sd, _handler = session_fixture
        chapters = _three_tier_chapters()
        sd.cached_history_chapters = chapters
        assert sd.cached_history_chapters is chapters
        assert all(isinstance(ch, HistoryChapter) for ch in sd.cached_history_chapters)


# ---------------------------------------------------------------------------
# Cadence — arc_tick fires from _execute_narration_turn at the right
# interaction count and not on intermediate turns.
# ---------------------------------------------------------------------------


def _wire_session_for_recompute(sd, *, interaction_pre_call: int, round_value: int) -> None:
    """Configure a session_fixture-style ``_SessionData`` so the next
    call to ``_execute_narration_turn`` lands the post-bump
    ``interaction`` exactly at ``interaction_pre_call + 1``.

    The fixture's TurnManager starts at interaction=1; the dispatch
    path always calls ``record_interaction`` for non-opening turns so
    the post-call value is the value the predicate sees.
    """

    sd.cached_history_chapters = _three_tier_chapters()
    sd.snapshot.turn_manager.interaction = interaction_pre_call
    sd.snapshot.turn_manager.round = round_value


@pytest.mark.asyncio
async def test_arc_tick_fires_at_cadence_boundary(otel_capture, session_fixture) -> None:
    """A turn that lands at a multiple of ``ARC_RECOMPUTE_INTERVAL``
    emits exactly one ``world_history.arc_tick`` span from the
    dispatch loop.

    Wire-first: this asserts the call site fires, not just the helper.
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(
            narration="Calm settles.", is_degraded=False, agent_duration_ms=1
        )
    )

    _wire_session_for_recompute(
        sd,
        interaction_pre_call=ARC_RECOMPUTE_INTERVAL - 1,
        round_value=10,
    )

    otel_capture.clear()
    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "I look around.", turn_context)

    ticks = [s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_tick"]
    assert len(ticks) == 1, (
        "arc_tick must fire exactly once when the post-record interaction "
        "lands at a cadence boundary. Spans seen: "
        f"{[s.name for s in otel_capture.get_finished_spans()]}"
    )


@pytest.mark.asyncio
async def test_arc_tick_does_not_fire_off_cadence(otel_capture, session_fixture) -> None:
    """A turn that lands between cadence boundaries emits no arc_tick
    span. The "bug Felix saw" — silent never-firing — is the opposite
    failure mode and is covered by ``test_arc_tick_fires_at_cadence_boundary``.
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(
            narration="Calm settles.", is_degraded=False, agent_duration_ms=1
        )
    )

    # Pre-call interaction is set so the post-call value is exactly
    # one less than the next cadence boundary — guaranteed off-cadence.
    _wire_session_for_recompute(
        sd,
        interaction_pre_call=ARC_RECOMPUTE_INTERVAL - 2,
        round_value=10,
    )

    otel_capture.clear()
    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "I wait.", turn_context)

    ticks = [s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_tick"]
    assert ticks == [], (
        f"arc_tick fired on an off-cadence turn. Spans seen: "
        f"{[s.name for s in otel_capture.get_finished_spans()]}"
    )


@pytest.mark.asyncio
async def test_arc_tick_attributes_carry_lie_detector_payload(
    otel_capture, session_fixture
) -> None:
    """Story 45-19 AC4: every arc_tick span carries the full payload
    the GM panel needs — interaction, round, from/to maturity, chapter
    counts, tier_changed, cadence_interval.
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="…", is_degraded=False, agent_duration_ms=1)
    )

    _wire_session_for_recompute(
        sd,
        interaction_pre_call=ARC_RECOMPUTE_INTERVAL - 1,
        round_value=10,
    )

    otel_capture.clear()
    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "again", turn_context)

    ticks = [s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_tick"]
    assert ticks
    attrs = ticks[0].attributes or {}
    for required in (
        "interaction",
        "round",
        "from_maturity",
        "to_maturity",
        "chapters_before",
        "chapters_after",
        "tier_changed",
        "cadence_interval",
    ):
        assert required in attrs, (
            f"arc_tick span missing required attribute {required!r}; got {sorted(attrs)}"
        )
    assert attrs["interaction"] == ARC_RECOMPUTE_INTERVAL
    assert attrs["cadence_interval"] == ARC_RECOMPUTE_INTERVAL


# ---------------------------------------------------------------------------
# Tier transition — arc_promoted fires once on Fresh→Early.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arc_promoted_fires_on_fresh_to_early_transition(
    otel_capture, session_fixture
) -> None:
    """Story 45-19 AC5: when the recompute crosses a tier boundary, the
    transition-only ``world_history.arc_promoted`` span fires once and
    carries the from/to maturity values.
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="…", is_degraded=False, agent_duration_ms=1)
    )

    # Round=10 (Early tier per ``CampaignMaturity.from_snapshot``);
    # snapshot.world_history starts empty (Fresh) so the recompute
    # crosses Fresh→Early.
    _wire_session_for_recompute(
        sd,
        interaction_pre_call=ARC_RECOMPUTE_INTERVAL - 1,
        round_value=10,
    )

    otel_capture.clear()
    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "transition turn", turn_context)

    promoted = [
        s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_promoted"
    ]
    assert len(promoted) == 1, (
        "arc_promoted must fire exactly once on a Fresh→Early transition. "
        f"Spans seen: {[s.name for s in otel_capture.get_finished_spans()]}"
    )
    attrs = promoted[0].attributes or {}
    assert attrs.get("from_maturity") == CampaignMaturity.Fresh.value
    assert attrs.get("to_maturity") == CampaignMaturity.Early.value


# ---------------------------------------------------------------------------
# Idempotency past Veteran — the bug Felix saw at turn 72 must not
# recur at higher turn counts. The recompute keeps firing (lie
# detector) but does not grow the chapter set or report a tier change.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arc_tick_at_turn_100_is_idempotent_no_op(otel_capture, session_fixture) -> None:
    """Story 45-19 AC3: at turn 100 the maturity is Veteran (the top
    tier) and the recompute is a no-op confirmation. The tick still
    fires so the GM panel has continuous coverage, but
    ``tier_changed=False`` and the chapter count is stable.
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="…", is_degraded=False, agent_duration_ms=1)
    )

    _wire_session_for_recompute(
        sd,
        interaction_pre_call=ARC_RECOMPUTE_INTERVAL - 1,
        round_value=100,  # Veteran tier
    )
    # Pre-materialise so the second tick is genuinely stable. Without
    # this the world_history starts empty, so the first tick would
    # report a Fresh→Veteran transition.
    from sidequest.game.world_materialization import materialize_world

    materialize_world(sd.snapshot, sd.cached_history_chapters)
    chapter_count_pre = len(sd.snapshot.world_history)

    otel_capture.clear()
    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "patrol", turn_context)

    ticks = [s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_tick"]
    assert len(ticks) == 1
    attrs = ticks[0].attributes or {}
    assert attrs.get("tier_changed") is False, (
        "Stable-Veteran recompute must not report a tier change"
    )
    assert attrs.get("chapters_before") == chapter_count_pre
    assert attrs.get("chapters_after") == chapter_count_pre
    assert len(sd.snapshot.world_history) == chapter_count_pre

    promoted = [
        s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_promoted"
    ]
    assert promoted == [], "arc_promoted must NOT fire on a stable-tier no-op recompute"


# ---------------------------------------------------------------------------
# Empty-chapter wiring — when the pack ships no history, the recompute
# is a graceful no-op. The fix must not regress sessions whose pack
# never had a history.yaml.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_skips_when_no_cached_chapters(otel_capture, session_fixture) -> None:
    """A session whose pack ships no ``history.yaml`` has an empty
    ``cached_history_chapters``. The dispatch loop must still run the
    cadence check (so the OTEL panel sees a tick happened) without
    crashing on an empty list.

    Asserts the recompute does not raise and emits at most one tick
    span (with ``chapters_before == chapters_after == 0``).
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="…", is_degraded=False, agent_duration_ms=1)
    )

    sd.cached_history_chapters = []
    sd.snapshot.turn_manager.interaction = ARC_RECOMPUTE_INTERVAL - 1
    sd.snapshot.turn_manager.round = 10

    otel_capture.clear()
    turn_context = _build_turn_context_for_test(sd)
    # Must not raise — empty chapter list is a legitimate session shape.
    await handler._execute_narration_turn(sd, "look", turn_context)

    ticks = [s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_tick"]
    # Either the predicate fired (one tick with zero chapters) or the
    # implementation chose to short-circuit on an empty chapter list
    # (no tick). Both are acceptable; what matters is that the call
    # site does not crash and the chapter counts (if reported) are zero.
    assert len(ticks) <= 1
    if ticks:
        attrs = ticks[0].attributes or {}
        assert attrs.get("chapters_before") == 0
        assert attrs.get("chapters_after") == 0
