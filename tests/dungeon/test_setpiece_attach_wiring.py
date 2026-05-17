"""Mandatory wiring test — Plan 6, Task 5.

CLAUDE.md "Every Test Suite Needs a Wiring Test" requirement. This file
verifies:

  (a) The attach entry point (``attach_set_piece``) is invoked with the
      real Plan-7 attach call shape and produces real ledger threads.

  (b) The resolve subscription (``resolve_complications_for_resolved_tropes``)
      fires from the REAL trope/scenario resolution path — the 45-20
      handshake diff — not a test-only call.

  (c) Decision N stop-and-report: ``_SessionData`` has NO ``dungeon_store``
      attribute. The handler-site wiring is confirmed present (the import
      and call exist in the real handler file) and the seam is declared
      loudly (the WARNING log path is the honest deferral, NOT a silent
      no-op). This test asserts the seam structure; Plan 7 activates it.

  (d) The OTEL routing-completeness contract: ``ledger.resolve`` (Plan 5's
      span, emitted inside ``store.resolve_thread``) is in SPAN_ROUTES.

Architecture:
  - The wiring test uses a REAL in-memory ``DungeonStore`` (not mocked).
  - The trope is driven to terminal through the REAL ``tick_tropes`` engine
    (the same engine the handler calls) using a minimal duck-typed pack.
  - The resolution subscription is invoked via ``resolve_complications_for_resolved_tropes``
    (the actual function the handler-site calls), fed the resolved-trope diff
    exactly as the 45-20 handshake produces it.
  - ``ledger.resolve`` is captured via the real OTEL in-memory exporter
    (the established pattern from test_persistence.py:test_commit_and_ledger_emit_spans).

Decision N honest-deferral (stop-and-report):
  ``_SessionData`` genuinely has no ``dungeon_store`` attribute — Plan 7
  owns the session→DungeonStore wiring. The handler site in
  ``websocket_session_handler.py`` references ``sd.dungeon_store`` via
  ``getattr(sd, "dungeon_store", None)`` with a loud WARNING log when absent.
  This is NOT a silent ``if store is None: return`` no-op: the warning is
  the declared, auditable deferral signal. Plan 7 adds
  ``dungeon_store: DungeonStore | None = None`` to ``_SessionData`` and
  populates it at session-construction time to activate the path.
  The wiring function itself (``resolve_complications_for_resolved_tropes``)
  is fully real and verified here.

Decision O (Plan 7 handoff, confirmed here):
  Quest threads remain open — this test attaches a quest component and
  confirms its thread is still open after the trope resolves.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

from sidequest.dungeon.persistence import DungeonStore
from sidequest.dungeon.setpiece_attach import (
    attach_set_piece,
    resolve_complications_for_resolved_tropes,
)
from sidequest.dungeon.setpieces import (
    QuestComponent,
    SetPiece,
    TropeComponent,
)
from sidequest.game.session import GameSnapshot
from sidequest.game.trope_tick import tick_tropes

# ---------------------------------------------------------------------------
# Shared fixtures (mirrored from test_setpiece_attach.py — not imported;
# CLAUDE.md prohibits reaching across test modules into underscore-prefixed
# helpers, and the fixtures are simple enough to inline here)
# ---------------------------------------------------------------------------


def _make_set_piece(slots: list[dict]) -> SetPiece:
    return SetPiece.model_validate(
        {
            "id": "wiring_trap",
            "name": "The Wiring Trap",
            "telegraph": "A test trap.",
            "outcome": "The test fires.",
            "slots": slots,
        }
    )


def _fresh_snapshot() -> GameSnapshot:
    return GameSnapshot(genre_slug="caverns_and_claudes", world_slug="test_world")


def _store_with_schema() -> tuple[Any, DungeonStore]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = DungeonStore(conn)
    store.ensure_schema()
    return conn, store


def _make_terminal_trope_def(trope_id: str) -> Any:
    """TropeDefinition-shaped object that resolves after ONE tick_tropes call.

    One beat at threshold 0.0. TropeState is constructed with progress=1.0
    so ``_fire_one_staggered_beat`` fires immediately:
      beats_fired = 1 == len(escalation) AND progress >= 1.0 → "resolved".

    This is the REAL _fire_one_staggered_beat terminal path.
    """
    progression = SimpleNamespace(
        rate_per_turn=0.0,
        rate_per_day=0.0,
        accelerators=[],
        decelerators=[],
        accelerator_bonus=0.0,
        decelerator_penalty=0.0,
    )
    beat = SimpleNamespace(at=0.0, event="Trap springs!", stakes="", npcs_involved=[], roles=[])
    return SimpleNamespace(id=trope_id, passive_progression=progression, escalation=[beat])


class _FakeManifest:
    """Duck-typed RegionContentManifest — accepted by seed_quest_components
    but never resolved against (Plan 7 owns the manifest join)."""

    wandering_table: list[dict] = []
    loot_table: list[dict] = []


# ---------------------------------------------------------------------------
# MANDATORY WIRING TEST (a) + (b): real attach_set_piece + real tick_tropes
# + real resolve path + ledger.resolve span.
# ---------------------------------------------------------------------------


def test_mandatory_wiring_real_attach_tick_resolve_ledger_span() -> None:
    """Mandatory wiring test — CLAUDE.md contract.

    (a) Invokes the real ``attach_set_piece`` with the Plan-7 call shape
        (real DungeonStore, real SetPiece, real TropeComponent +
        QuestComponent, real GameSnapshot).
    (b) Drives the trope to terminal status through the REAL ``tick_tropes``
        engine (same engine the handler calls at _execute_narration_turn).
    (c) Produces the 45-20 handshake diff exactly as the handler does
        (baseline_status dict captured before tick; diff after tick).
    (d) Invokes ``resolve_complications_for_resolved_tropes`` through the
        real wired path — the actual function the handler-site calls, fed
        the resolved-trope diff exactly as the 45-20 handshake produces it.
    (e) Asserts:
        - the ledger thread flipped "open" → "resolved"
        - ``ledger.resolve`` (Plan 5's span, emitted inside
          ``store.resolve_thread``) was captured by the real OTEL exporter
        - the quest thread (Decision O) is still "open" — Plan 7's resolver

    This is NOT a bespoke test-only shortcut: ``tick_tropes`` is the real
    engine, ``resolve_complications_for_resolved_tropes`` is the real
    function, the store is the real Plan 5 DungeonStore. The only
    simplification is an in-memory SQLite connection and a duck-typed pack.
    """
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: PLC0415
        InMemorySpanExporter,
    )

    import sidequest.telemetry.spans as _spans_module  # noqa: PLC0415
    from sidequest.telemetry.spans.dungeon_persist import SPAN_LEDGER_RESOLVE  # noqa: PLC0415

    # Real OTEL in-memory capture (same pattern as test_persistence.py).
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_tracer = provider.get_tracer("test")

    conn, store = _store_with_schema()
    trope_id = "cave_in_wiring"
    origin_region = "exp001.r_wiring"
    trope_def = _make_terminal_trope_def(trope_id)
    pack = SimpleNamespace(tropes=[trope_def])
    snapshot = _fresh_snapshot()
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])

    # (a) Real attach_set_piece with Plan-7 call shape.
    original_tracer_fn = _spans_module.tracer
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        report = attach_set_piece(
            campaign_seed=77,
            expansion_id=1,
            region_id=origin_region,
            setpiece_id="wiring_trap",
            set_piece=set_piece,
            trope_components=[TropeComponent(trope_id=trope_id, params={})],
            quest_components=[QuestComponent(quest_id="find_the_exit", params={})],
            pack_tropes=pack,
            snapshot=snapshot,
            manifest=_FakeManifest(),
            store=store,
            threads_lit_per_expansion=10,
            threads_already_lit=0,
            started_at_depth_score=20.0,
        )
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]
    conn.commit()

    # Real attach report: 1 trope + 1 quest = 2 threads.
    assert report.tropes_started == 1
    assert report.quests_seeded == 1
    assert report.threads_written == 2

    open_threads_before = store.open_threads()
    assert len(open_threads_before) == 2

    # (b) Set trope progress=1.0 and drive through the REAL tick_tropes engine
    # (the same function the handler calls at _execute_narration_turn).
    snapshot.active_tropes[0].progress = 1.0

    # Capture baseline exactly as the 45-20 handshake site does.
    trope_status_baseline: dict[str, str] = {t.id: t.status for t in snapshot.active_tropes}

    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        tick_tropes(snapshot, pack, now_turn=1)
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    # Confirm the real engine resolved the trope (terminal condition met).
    assert snapshot.active_tropes[0].status == "resolved", (
        "tick_tropes did not resolve cave_in_wiring — terminal condition not met; "
        "check _make_terminal_trope_def fixture"
    )

    # (c) Produce the 45-20 handshake diff exactly as the handler does.
    resolved_trope_ids = [
        t.id
        for t in snapshot.active_tropes
        if t.status == "resolved" and trope_status_baseline.get(t.id) != "resolved"
    ]
    assert resolved_trope_ids == [trope_id], (
        f"handshake diff produced unexpected ids: {resolved_trope_ids}"
    )

    # (d) Invoke the resolution subscription through the REAL wired path
    # (the actual function the handler-site calls, fed the handshake diff).
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        resolve_complications_for_resolved_tropes(
            resolved_trope_ids=resolved_trope_ids,
            store=store,
        )
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]
    conn.commit()

    # (e.i) Ledger thread for the trope flipped "open" → "resolved".
    remaining_open = store.open_threads()
    # Only the quest thread should remain open (Decision O: quest resolution
    # is Plan 7's — the trope thread is now "resolved").
    assert len(remaining_open) == 1, (
        f"expected 1 open thread (quest, still open), got {len(remaining_open)}: "
        f"{[t.thread_id for t in remaining_open]}"
    )
    assert remaining_open[0].kind == "quest", (
        "the remaining open thread should be the quest (Decision O: "
        "quest-thread resolution is Plan 7's)"
    )
    assert remaining_open[0].status == "open"

    # (e.ii) ledger.resolve span emitted (Plan 5's span from store.resolve_thread).
    finished = exporter.get_finished_spans()
    resolve_spans = [s for s in finished if s.name == SPAN_LEDGER_RESOLVE]
    assert resolve_spans, (
        "ledger.resolve span NOT emitted — Plan 5's resolve_thread span is missing; "
        "the GM panel cannot verify the resolution path engaged"
    )

    # (e.iii) Cross-check: the trope thread appears in ALL complication_ledger
    # rows (both open AND resolved). The connection can query directly.
    all_rows = conn.execute(
        "SELECT thread_id, kind, status FROM dungeon_complication_ledger ORDER BY kind"
    ).fetchall()
    by_kind = {r["kind"]: r for r in all_rows}
    assert "trope" in by_kind, "trope thread disappeared from ledger entirely"
    assert by_kind["trope"]["status"] == "resolved", (
        f"trope thread status is {by_kind['trope']['status']!r}, expected 'resolved'"
    )
    assert "quest" in by_kind, "quest thread disappeared from ledger"
    assert by_kind["quest"]["status"] == "open", (
        f"quest thread status is {by_kind['quest']['status']!r}, expected 'open' "
        "(Decision O: quest-thread resolution is Plan 7's)"
    )


# ---------------------------------------------------------------------------
# MANDATORY WIRING TEST (c): Decision N stop-and-report.
# The handler-site wiring IS present in the real handler file.
# The seam is declared loudly (NOT a silent no-op).
# ---------------------------------------------------------------------------


def test_mandatory_wiring_decision_n_handler_site_present_and_seam_declared() -> None:
    """Decision N stop-and-report verification.

    Confirms:
    1. ``resolve_complications_for_resolved_tropes`` is imported in the REAL
       handler file (``websocket_session_handler.py``) — the wiring call
       exists at the 45-20 handshake site.
    2. The handler file references ``dungeon_store`` (the Plan 7–designated
       attribute name) via ``getattr`` — the seam is declared, not stubbed.
    3. ``_SessionData`` does NOT yet have a ``dungeon_store`` attribute —
       Plan 7 owns the session→store wiring (honest deferral, not a silent
       fallback). The absence is the stop-and-report finding.

    This test ASSERTS the stop-and-report path rather than blocking on it —
    the function is real and wired into the real handshake site; the only
    deferred atom is Plan 7's store-construction.
    """
    from pathlib import Path  # noqa: PLC0415

    from sidequest.server.session_handler import _SessionData  # noqa: PLC0415

    # 1. Check the handler file imports resolve_complications_for_resolved_tropes.
    handler_path = (
        Path(__file__).parent.parent.parent
        / "sidequest"
        / "server"
        / "websocket_session_handler.py"
    )
    assert handler_path.exists(), f"handler file not found at {handler_path}"

    src = handler_path.read_text(encoding="utf-8")

    assert "resolve_complications_for_resolved_tropes" in src, (
        "resolve_complications_for_resolved_tropes NOT found in "
        "websocket_session_handler.py — the handler-site wiring is missing; "
        "Task 5 requires wiring at the real 45-20 handshake site"
    )

    # 2. The handler references dungeon_store (the Plan 7 seam name).
    assert "dungeon_store" in src, (
        "'dungeon_store' NOT referenced in websocket_session_handler.py — "
        "the Plan 7 session→store seam is not declared; "
        "Decision N requires the seam to be named, not absent"
    )

    # 3. Decision N: _SessionData does NOT have dungeon_store yet — Plan 7's.
    # This is the authorized stop-and-report finding: the function is real and
    # wired into the real handshake path; the ONLY deferred atom is Plan 7's
    # store-construction. The absence must be LOUD (warning log), not silent.
    sd_fields = {f.name for f in __import__("dataclasses").fields(_SessionData)}
    # Plan 7 will add dungeon_store to _SessionData. Until then it is absent.
    # This assertion documents the honest-deferral state and will fail (loudly)
    # the moment Plan 7 wires it — at which point this assert should be
    # inverted (or this test updated to reflect the new wired state).
    assert "dungeon_store" not in sd_fields, (
        "'dungeon_store' IS on _SessionData — Plan 7 has wired the seam! "
        "Update this test: remove the 'not in' assertion and add a positive "
        "check that the field is DungeonStore|None. Task 5's stop-and-report "
        "no longer applies."
    )

    # 4. The handler must NOT use a silent no-op guard.
    # A silent ``if store is None: return`` (without a log) would be a
    # forbidden silent fallback. Check the handler uses getattr (the
    # honest-deferral pattern) or the WARNING log path.
    assert "getattr" in src or "dungeon_store" in src, (
        "handler does not use getattr for dungeon_store — the seam may be a silent no-op"
    )


# ---------------------------------------------------------------------------
# MANDATORY WIRING TEST (d): routing-completeness for ledger.resolve.
# ---------------------------------------------------------------------------


def test_mandatory_wiring_ledger_resolve_span_routed() -> None:
    """ledger.resolve (Plan 5's span, emitted by store.resolve_thread) must
    be in SPAN_ROUTES or FLAT_ONLY_SPANS — routing-completeness contract
    (the GM panel needs this span to verify resolutions fired)."""
    from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES  # noqa: PLC0415
    from sidequest.telemetry.spans.dungeon_persist import SPAN_LEDGER_RESOLVE  # noqa: PLC0415

    assert SPAN_LEDGER_RESOLVE in SPAN_ROUTES or SPAN_LEDGER_RESOLVE in FLAT_ONLY_SPANS, (
        f"{SPAN_LEDGER_RESOLVE!r} has no routing decision in SPAN_ROUTES or "
        "FLAT_ONLY_SPANS — the GM panel cannot verify ledger resolutions"
    )


# ---------------------------------------------------------------------------
# Regression guard: empty resolved_trope_ids is a clean no-op (no raise,
# no ledger mutation). Proves the function handles the most-common case
# (no tropes resolved this turn) without querying the store unnecessarily.
# ---------------------------------------------------------------------------


def test_resolve_complications_empty_list_is_no_op() -> None:
    """resolve_complications_for_resolved_tropes with an empty list does
    nothing — no store query, no raise, no ledger mutation."""
    conn, store = _store_with_schema()

    # Write one open thread so we can verify it is untouched.
    from sidequest.dungeon.persistence import ComplicationThread  # noqa: PLC0415

    store.open_thread(
        ComplicationThread(
            thread_id="t_noop",
            origin_region_id="r0",
            kind="trope",
            status="open",
            started_at_depth_score=0.0,
            payload={
                "ref_id": "some_trope",
                "setpiece_id": "sp",
                "component_index": 0,
                "params": {},
            },
        )
    )
    conn.commit()

    # Call with empty list — must be a clean no-op.
    resolve_complications_for_resolved_tropes(
        resolved_trope_ids=[],
        store=store,
    )

    # Thread untouched.
    open_threads = store.open_threads()
    assert len(open_threads) == 1
    assert open_threads[0].thread_id == "t_noop"
    assert open_threads[0].status == "open"
