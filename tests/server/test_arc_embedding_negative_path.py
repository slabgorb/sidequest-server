"""Story 45-23 — negative-path wire test for the arc-embedding writeback
pipeline.

The bug Felix saw was a SILENT absence — no spans, no entries, just
nothing. The fix must close that absence on tier-promotion turns
(covered by ``test_arc_embedding_writeback_wire.py``) AND must NOT
introduce a different silent failure on non-promotion turns: spans
firing with zero counts every cadence tick would create noise the GM
panel cannot interpret.

The seam between 45-19 and 45-23 is:

- 45-19's ``arc_tick`` always fires on a cadence tick (the lie-
  detector — the panel sees the recompute happened).
- 45-19's ``arc_promoted`` fires only on a tier transition.
- 45-23's ``arc_embedding_seed`` / ``narrative_log_writeback`` /
  ``lore_writeback`` fire ONLY when 45-19 reports new chapters added.

This test pins that boundary. A Veteran-stable recompute
(``tier_changed=False``, ``chapters_added=[]``) must NOT emit any
45-23 spans and must NOT mutate ``lore_store`` or
``snapshot.narrative_log`` from the recompute path. Per
context-story-45-23.md AC4.
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
from sidequest.game.history_chapter import (
    ChapterNarrativeEntry,
    HistoryChapter,
)
from sidequest.game.lore_store import LoreStore
from sidequest.game.world_materialization import (
    ARC_RECOMPUTE_INTERVAL,
    materialize_world,
)
from sidequest.telemetry.setup import init_tracer
from tests.server.conftest import _build_turn_context_for_test


@pytest.fixture
def otel_capture():
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


def _content_chapters() -> list[HistoryChapter]:
    """Three-tier chapters with content on every tier so we can prove
    that even a fully-content-bearing chapter list doesn't trigger
    seeding when the recompute reports no diff.
    """

    return [
        HistoryChapter(
            id="early",
            label="Early arc",
            narrative_log=[
                ChapterNarrativeEntry(
                    speaker="narrator", text="Early arc opening."
                ),
            ],
            lore=["Early-tier fact."],
        ),
        HistoryChapter(
            id="mid",
            label="Mid arc",
            narrative_log=[
                ChapterNarrativeEntry(
                    speaker="narrator", text="Mid arc opening."
                ),
            ],
            lore=["Mid-tier fact."],
        ),
        HistoryChapter(
            id="veteran",
            label="Veteran arc",
            narrative_log=[
                ChapterNarrativeEntry(
                    speaker="narrator", text="Veteran arc opening."
                ),
            ],
            lore=["Veteran-tier fact."],
        ),
    ]


@pytest.mark.asyncio
async def test_no_promotion_no_arc_embedding_seed_span(
    session_fixture, otel_capture
) -> None:
    """A turn at Veteran-stable lands at a cadence boundary (so 45-19's
    ``arc_tick`` fires) but no tier change (so ``arc_promoted`` does
    NOT fire and ``chapters_added=[]``). 45-23's seed span must also
    NOT fire — the boundary between ``arc_tick`` (always) and
    ``arc_embedding_seed`` (promotion-only).
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(
            narration="Patrol completes.",
            is_degraded=False,
            agent_duration_ms=1,
        )
    )

    sd.cached_history_chapters = _content_chapters()
    sd.snapshot.turn_manager.interaction = ARC_RECOMPUTE_INTERVAL - 1
    sd.snapshot.turn_manager.round = 100  # Veteran tier
    # Pre-materialize the world so the next recompute is genuinely
    # stable. Without this the world_history starts empty (Fresh) and
    # the first tick reports a Fresh→Veteran transition (which would
    # trigger seeding — not what this test is probing).
    materialize_world(sd.snapshot, sd.cached_history_chapters)
    sd.lore_store = LoreStore()  # clean slate so the assertion is sharp.

    otel_capture.clear()
    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "patrol", turn_context)

    span_names = [s.name for s in otel_capture.get_finished_spans()]

    # 45-19's arc_tick always fires on a cadence boundary — the
    # lie-detector signal. Its presence guarantees the recompute path
    # ran; the test below asserts the seeding path stayed silent.
    assert "world_history.arc_tick" in span_names, (
        "Test setup mismatch: 45-19's arc_tick must fire on a cadence "
        "boundary so the negative assertion below is meaningful. "
        f"Spans seen: {span_names}"
    )
    # 45-19's arc_promoted MUST NOT fire (stable tier).
    assert "world_history.arc_promoted" not in span_names, (
        "Test setup mismatch: arc_promoted fired on a stable-Veteran "
        "tick. The negative test below assumes no transition. "
        f"Spans seen: {span_names}"
    )

    # The actual 45-23 negative assertions:
    assert "world_history.arc_embedding_seed" not in span_names, (
        "arc_embedding_seed fired on a no-promotion tick — would create "
        "GM-panel noise the operator cannot interpret. The seam between "
        "45-19 (always-tick) and 45-23 (promotion-only) is broken. "
        f"Spans seen: {span_names}"
    )
    assert "world_history.narrative_log_writeback" not in span_names, (
        f"narrative_log_writeback fired without a promotion. Spans: {span_names}"
    )
    assert "world_history.lore_writeback" not in span_names, (
        f"lore_writeback fired without a promotion. Spans: {span_names}"
    )


@pytest.mark.asyncio
async def test_no_promotion_no_arc_lore_fragments_added(
    session_fixture, otel_capture
) -> None:
    """A no-promotion tick must not mint new ``lore_arc_*`` fragments
    on ``lore_store``. Even with content-bearing chapters in the cache,
    the helper consumes only the empty diff.
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(
            narration="Patrol completes.",
            is_degraded=False,
            agent_duration_ms=1,
        )
    )

    sd.cached_history_chapters = _content_chapters()
    sd.snapshot.turn_manager.interaction = ARC_RECOMPUTE_INTERVAL - 1
    sd.snapshot.turn_manager.round = 100  # Veteran
    materialize_world(sd.snapshot, sd.cached_history_chapters)
    sd.lore_store = LoreStore()

    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "patrol", turn_context)

    arc_ids = [
        fid for fid in sd.lore_store.fragments
        if fid.startswith("lore_arc_")
    ]
    assert arc_ids == [], (
        "No-promotion tick minted lore_arc_* fragments — the helper "
        f"is not consuming the chapters_added diff. Got: {arc_ids}"
    )


@pytest.mark.asyncio
async def test_no_promotion_no_arc_promotion_narrative_entries(
    session_fixture, otel_capture
) -> None:
    """A no-promotion tick must not append arc-promotion entries to
    ``snapshot.narrative_log``. The per-turn narrator + player append
    paths still write their normal (non-arc) entries, so we filter on
    the entry_type tag to isolate the seeding path's output.
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(
            narration="Patrol completes.",
            is_degraded=False,
            agent_duration_ms=1,
        )
    )

    sd.cached_history_chapters = _content_chapters()
    sd.snapshot.turn_manager.interaction = ARC_RECOMPUTE_INTERVAL - 1
    sd.snapshot.turn_manager.round = 100
    materialize_world(sd.snapshot, sd.cached_history_chapters)

    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "patrol", turn_context)

    arc_entries = [
        e for e in sd.snapshot.narrative_log
        if e.entry_type == "arc_promotion"
    ]
    assert arc_entries == [], (
        "No-promotion tick wrote arc-promotion narrative entries — the "
        "seeding path engaged when it should have been silent. "
        f"Got: {arc_entries!r}"
    )


@pytest.mark.asyncio
async def test_off_cadence_turn_emits_no_45_23_spans(
    session_fixture, otel_capture
) -> None:
    """Off-cadence turn — 45-19's ``arc_tick`` doesn't fire (the
    cadence predicate is false), so 45-23's seed path also has no
    chance to engage. Pin this so a future implementation that
    fires the seeding helper unconditionally (rather than gated on
    the tick) trips a hard test failure.
    """

    sd, handler = session_fixture
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(
            narration="…",
            is_degraded=False,
            agent_duration_ms=1,
        )
    )

    sd.cached_history_chapters = _content_chapters()
    # Pre-call interaction = ARC_RECOMPUTE_INTERVAL - 2; post-bump
    # value is one less than the next cadence boundary — guaranteed
    # off-cadence.
    sd.snapshot.turn_manager.interaction = ARC_RECOMPUTE_INTERVAL - 2
    sd.snapshot.turn_manager.round = 10
    sd.lore_store = LoreStore()

    otel_capture.clear()
    turn_context = _build_turn_context_for_test(sd)
    await handler._execute_narration_turn(sd, "wait", turn_context)

    span_names = [s.name for s in otel_capture.get_finished_spans()]
    assert "world_history.arc_tick" not in span_names, (
        "Test setup mismatch: arc_tick fired off-cadence. "
        f"Spans seen: {span_names}"
    )
    for forbidden in (
        "world_history.arc_embedding_seed",
        "world_history.narrative_log_writeback",
        "world_history.lore_writeback",
    ):
        assert forbidden not in span_names, (
            f"{forbidden} fired off-cadence — the seeding helper "
            "must be gated on the recompute tick. "
            f"Spans seen: {span_names}"
        )
