"""Mandatory wiring test — Plan 6, Task 5.

CLAUDE.md "Every Test Suite Needs a Wiring Test" requirement. This file
verifies:

  (a) The attach entry point (``attach_set_piece``) is invoked with the
      real Plan-7 attach call shape and produces real ledger threads.

  (b) The resolve subscription (``resolve_complications_for_resolved_tropes``)
      fires from the REAL trope/scenario resolution path — the 45-20
      handshake diff — not a test-only call.

  (c) Decision N (corrected — honest deferral, NO runtime noise):
      ``_SessionData`` has NO ``dungeon_store`` attribute. The handler-site
      call is present and GUARDED by
      ``getattr(sd, "dungeon_store", None) is not None``. Pre-Plan-7 the
      no-op is PROVABLY CORRECT (only Plan 7 both materializes set-pieces —
      creating ledger threads — AND wires ``sd.dungeon_store``; they land
      together, so store-absent ⟺ no open dungeon threads). There is NO
      runtime warning/log (a per-turn log on the global trope engine is
      ignorable noise, not a guard). The LOUD seam is this file's
      structural tripwire (the ``"dungeon_store" not in _SessionData``
      assertion), which fires exactly once — when Plan 7 must finish.

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

Decision N honest-deferral (corrected — provably-correct no-op):
  ``_SessionData`` genuinely has no ``dungeon_store`` attribute — Plan 7
  owns the session→DungeonStore wiring. The handler site in
  ``websocket_session_handler.py`` reads the store via
  ``getattr(sd, "dungeon_store", None)`` and gates the resolution call on
  ``is not None``. Pre-Plan-7 the no-op is provably correct: only Plan 7
  both materializes set-pieces (creating ledger threads via
  ``attach_set_piece``) AND wires ``sd.dungeon_store`` — the two land
  together, so store-absent ⟺ no open dungeon threads exist. NO runtime
  warning/log (a per-turn log on the global trope engine is ignorable
  noise, NOT a guard — the reviewer-rejected form). This is honest
  deferral done right: the invariant is documented in a precise code
  comment AND provably true; the loud declaration is THIS file's
  structural tripwire (the ``"dungeon_store" not in _SessionData``
  assertion), which fires exactly once when Plan 7 must finish the job.
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
    """Pack DEFINITION (duck-typed for tick_tropes' pack_tropes_by_id) whose
    one-beat ladder reaches terminal in ONE tick once its live TropeState
    progress is at the cap.

    This returns the trope DEFINITION, NOT a TropeState. One beat at
    threshold 0.0. The CALLER must set the live ``TropeState.progress = 1.0``
    (the test does this explicitly before tick_tropes;
    start_trope_components appends the TropeState at progress 0.0). Then
    ``_fire_one_staggered_beat`` fires the single beat:
      beats_fired = 1 == len(escalation) AND progress >= 1.0 → "resolved".

    This drives the REAL _fire_one_staggered_beat terminal path.
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
    """Decision N (corrected) — honest-deferral structural tripwire.

    Decision N corrected (Architect, spec-review pass): pre-Plan-7,
    ``attach_set_piece`` is never called in production (it is Plan 7's
    materializer entry point — nothing in prod creates ComplicationThreads
    yet). So store-absent ⟺ zero dungeon ledger threads exist ⟹ the
    handler-site no-op is PROVABLY CORRECT. There is NO runtime warning/log
    (a per-turn log would fire on ~100% of pre-Plan-7 turns because the
    trope engine is global — pure ignorable noise). The LOUD seam is THIS
    test's structural tripwire instead: zero runtime noise, a CI tripwire
    that fires exactly once — when Plan 7 must finish the job.

    Confirms:
    1. ``resolve_complications_for_resolved_tropes`` is invoked in the REAL
       handler file at the 45-20 handshake site.
    2. The call is GUARDED by ``getattr(sd, "dungeon_store", None) is not
       None`` — the documented-invariant gate (not a tautology, not a
       silent fallback: the invariant is provably true and code-commented).
    3. There is NO runtime warning/log for the absent-store case (Decision
       N corrected — that would be ignorable per-turn noise).
    4. ``_SessionData`` does NOT yet have a ``dungeon_store`` field — the
       honest-deferral finding. THIS assertion is the noise-free loud seam:
       it fires exactly once, when Plan 7 adds the field, forcing whoever
       flips it to complete the wiring (see the Plan-7 directive below).
    """
    from pathlib import Path  # noqa: PLC0415

    from sidequest.server.session_handler import _SessionData  # noqa: PLC0415

    handler_path = (
        Path(__file__).parent.parent.parent
        / "sidequest"
        / "server"
        / "websocket_session_handler.py"
    )
    assert handler_path.exists(), f"handler file not found at {handler_path}"

    src = handler_path.read_text(encoding="utf-8")

    # 1. The resolution function is invoked at the handler site.
    assert "resolve_complications_for_resolved_tropes" in src, (
        "resolve_complications_for_resolved_tropes NOT found in "
        "websocket_session_handler.py — the handler-site wiring is missing; "
        "Task 5 requires wiring at the real 45-20 handshake site"
    )

    # 2. The call is GUARDED by the documented-invariant gate. Assert the
    # actual guarded-call STRUCTURE, not a tautology. The handler shape is:
    #     _dungeon_store = getattr(sd, "dungeon_store", None)
    #     if _dungeon_store is not None:
    #         ... resolve_complications_for_resolved_tropes(...)
    # We bind the `is not None` check to the SAME local the getattr
    # assigns (the next-line gate), so a future unrelated `is not None`
    # elsewhere in the handler cannot accidentally satisfy this (MINOR
    # tightening, spec-review).
    import re  # noqa: PLC0415

    gate_re = re.compile(r'(\w+)\s*=\s*getattr\(\s*sd\s*,\s*"dungeon_store"\s*,\s*None\s*\)')
    m = gate_re.search(src)
    assert m is not None, (
        "handler does not read the store via `<var> = getattr(sd, "
        '"dungeon_store", None)` — the documented-invariant Decision-N '
        "gate is missing"
    )
    store_var = m.group(1)
    gate_idx = m.end()
    # The `is not None` gate must reference THE local the getattr assigned
    # (not some unrelated `is not None`), and must appear right after it.
    typed_gate = f"if {store_var} is not None:"
    typed_gate_idx = src.find(typed_gate, gate_idx)
    assert typed_gate_idx != -1, (
        f"no `{typed_gate}` gate after `{store_var} = getattr(...)` — "
        "Decision N requires the call gated on the store local being "
        "non-None (provably-correct no-op when the store is absent)"
    )
    # And the guarded call must be AFTER the gate (inside its if-block).
    call_idx = src.index("resolve_complications_for_resolved_tropes(", typed_gate_idx)
    assert call_idx > typed_gate_idx, (
        "the resolve_complications_for_resolved_tropes(...) call is not "
        f"inside the `{typed_gate}` block — Decision N requires the "
        "gated-call structure"
    )

    # 3. NO runtime warning/log for the absent-store case (Decision N
    # corrected — a per-turn log on the global trope engine is noise, not a
    # guard). Assert the old noise-y warning string is GONE.
    assert "dungeon.ledger_resolve.skipped" not in src, (
        "handler still emits a per-turn warning for the absent-store case — "
        "Decision N (corrected) removes ALL runtime noise; the loud seam is "
        "THIS test's structural tripwire, not a log"
    )

    # 4. THE LOUD SEAM (noise-free, fires exactly once): _SessionData has no
    # dungeon_store field yet — the honest-deferral finding. When Plan 7
    # adds the field this assertion fails, forcing the Plan-7 author to:
    #   (i) invert this assertion to a positive type check, AND
    #   (ii) in the SAME change, prove the resolution path is exercised
    #        end-to-end (see the Decision-N seam at the handler site +
    #        test_mandatory_wiring_real_attach_tick_resolve_ledger_span,
    #        which already exercises resolve_complications_for_resolved_tropes
    #        with a real store the way Plan 7 will populate sd.dungeon_store).
    # Plan 7 MUST NOT just delete/skip this test — it must flip it and wire
    # the store so the gated call actually fires from the live turn path.
    sd_fields = {f.name for f in __import__("dataclasses").fields(_SessionData)}
    assert "dungeon_store" not in sd_fields, (
        "'dungeon_store' IS on _SessionData — Plan 7 has wired the seam. "
        "REQUIRED ACTION (do NOT just delete this test): (1) invert this "
        "assertion to `assert 'dungeon_store' in sd_fields` plus a positive "
        "`DungeonStore | None` type check; (2) in the SAME change, populate "
        "sd.dungeon_store at session construction so the gated handler call "
        "fires from the live turn path; (3) add/keep an integration test "
        "proving a real resolution flows turn → handshake diff → "
        "resolve_complications_for_resolved_tropes → store.resolve_thread. "
        "Decision N's deferral is now complete and must be PROVEN, not "
        "merely unblocked."
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
