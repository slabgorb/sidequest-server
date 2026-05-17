"""Beneath Sünden Plan 7 Task 1 — MaterializationRequest + pipeline skeleton tests.

Two test bullets from the plan:
  1. Request validation: expansion_id < 1 raises ValueError, frontier_edge not in
     the supplied frontier raises ValueError, burst_magnitude < 1 raises ValueError
     — loud, no defaults, no silent fallbacks.
  2. materialize() opens a parent dungeon.materialize span; the five child stage
     spans (design, fill, curate, attach, commit) nest under it in order.
     Each stage raises NotImplementedError (skeleton contract).
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from sidequest.dungeon.persistence import FrontierEdge  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _make_frontier_edge(frontier_edge_id: str = "fe1") -> FrontierEdge:
    return FrontierEdge(
        frontier_edge_id=frontier_edge_id,
        from_region_id="exp001.r0",
        heading="north",
        spawn_depth_score=15.0,
    )


def _otel_in_memory() -> tuple[Any, Any, Any]:
    """Return (exporter, provider, real_tracer) for in-memory OTEL tests."""
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: PLC0415
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_tracer = provider.get_tracer("test")
    return exporter, provider, real_tracer


# ---------------------------------------------------------------------------
# Test 1: Request validation
# ---------------------------------------------------------------------------


class TestMaterializationRequestValidation:
    """Task 1 test bullet 1: the request is a frozen, hashable value object
    that rejects invalid inputs loudly at construction — no silent defaults."""

    def _valid_kwargs(self, frontier_edge: FrontierEdge | None = None) -> dict:
        fe = frontier_edge or _make_frontier_edge("fe1")
        return {
            "campaign_seed": 7,
            "expansion_id": 1,
            "frontier_edge": fe,
            "frontier": [fe],
            "attach_region_ids": ["exp001.r0"],
            "heading": "north",
            "burst_magnitude": 3,
            "lookahead_breadth": 2,
        }

    def test_valid_request_constructs_without_error(self) -> None:
        from sidequest.dungeon.materializer import MaterializationRequest

        req = MaterializationRequest.build(**self._valid_kwargs())
        assert req.expansion_id == 1
        assert req.campaign_seed == 7
        assert req.burst_magnitude == 3
        assert req.lookahead_breadth == 2

    def test_expansion_id_zero_raises_value_error(self) -> None:
        """Expansion 0 is the seed/entrance; it is reserved. expansion_id < 1 is invalid."""
        from sidequest.dungeon.materializer import MaterializationRequest

        kwargs = self._valid_kwargs()
        kwargs["expansion_id"] = 0
        with pytest.raises(ValueError, match="expansion_id"):
            MaterializationRequest.build(**kwargs)

    def test_expansion_id_negative_raises_value_error(self) -> None:
        from sidequest.dungeon.materializer import MaterializationRequest

        kwargs = self._valid_kwargs()
        kwargs["expansion_id"] = -5
        with pytest.raises(ValueError, match="expansion_id"):
            MaterializationRequest.build(**kwargs)

    def test_frontier_edge_not_in_frontier_raises_value_error(self) -> None:
        """frontier_edge must be a member of the supplied frontier list."""
        from sidequest.dungeon.materializer import MaterializationRequest

        fe_in_frontier = _make_frontier_edge("fe1")
        fe_not_in_frontier = _make_frontier_edge("fe_unknown")
        kwargs = self._valid_kwargs(frontier_edge=fe_in_frontier)
        kwargs["frontier_edge"] = fe_not_in_frontier
        kwargs["frontier"] = [fe_in_frontier]  # fe_not_in_frontier not in this list

        with pytest.raises(ValueError, match="frontier"):
            MaterializationRequest.build(**kwargs)

    def test_burst_magnitude_zero_raises_value_error(self) -> None:
        from sidequest.dungeon.materializer import MaterializationRequest

        kwargs = self._valid_kwargs()
        kwargs["burst_magnitude"] = 0
        with pytest.raises(ValueError, match="burst_magnitude"):
            MaterializationRequest.build(**kwargs)

    def test_burst_magnitude_negative_raises_value_error(self) -> None:
        from sidequest.dungeon.materializer import MaterializationRequest

        kwargs = self._valid_kwargs()
        kwargs["burst_magnitude"] = -1
        with pytest.raises(ValueError, match="burst_magnitude"):
            MaterializationRequest.build(**kwargs)

    def test_request_is_frozen(self) -> None:
        """frozen=True dataclass — attribute assignment must raise
        FrozenInstanceError (a subclass of AttributeError)."""
        from sidequest.dungeon.materializer import MaterializationRequest

        req = MaterializationRequest.build(**self._valid_kwargs())
        with pytest.raises(AttributeError):
            req.expansion_id = 99  # type: ignore[misc]

    def test_request_is_hashable(self) -> None:
        """The object must be usable as a dict key or set member."""
        from sidequest.dungeon.materializer import MaterializationRequest

        req = MaterializationRequest.build(**self._valid_kwargs())
        d: dict[MaterializationRequest, str] = {req: "ok"}
        assert d[req] == "ok"
        s: set[MaterializationRequest] = {req}
        assert req in s

    def test_attach_region_ids_stored_as_tuple(self) -> None:
        """list input is frozen to tuple internally so the object stays hashable."""
        from sidequest.dungeon.materializer import MaterializationRequest

        req = MaterializationRequest.build(**self._valid_kwargs())
        # Must be a tuple (not list) — lists are unhashable and would break
        # the hash contract.
        assert isinstance(req.attach_region_ids, tuple)

    def test_two_equal_requests_have_same_hash(self) -> None:
        from sidequest.dungeon.materializer import MaterializationRequest

        fe = _make_frontier_edge("fe1")
        kwargs = {
            "campaign_seed": 7,
            "expansion_id": 1,
            "frontier_edge": fe,
            "frontier": [fe],
            "attach_region_ids": ["exp001.r0"],
            "heading": "north",
            "burst_magnitude": 3,
            "lookahead_breadth": 2,
        }
        r1 = MaterializationRequest.build(**kwargs)
        r2 = MaterializationRequest.build(**kwargs)
        assert r1 == r2
        assert hash(r1) == hash(r2)


# ---------------------------------------------------------------------------
# Test 2: span nesting — parent + five ordered children
# ---------------------------------------------------------------------------


class TestMaterializePipelineSpans:
    """Task 1 test bullet 2: materialize() opens a dungeon.materialize parent
    span; the five stage child spans (design, fill, curate, attach, commit) nest
    under it in order. Each stage raises NotImplementedError (skeleton contract).
    """

    def _build_request(self) -> Any:
        from sidequest.dungeon.materializer import MaterializationRequest

        fe = _make_frontier_edge("fe1")
        return MaterializationRequest.build(
            campaign_seed=7,
            expansion_id=1,
            frontier_edge=fe,
            frontier=[fe],
            attach_region_ids=["exp001.r0"],
            heading="north",
            burst_magnitude=3,
            lookahead_breadth=2,
        )

    def test_materialize_raises_not_implemented_error(self) -> None:
        """At Task 1 each stage raises NotImplementedError — materialize itself
        propagates the first one it hits (design stage)."""
        import sidequest.telemetry.spans as _spans_module
        from sidequest.dungeon.materializer import materialize
        from sidequest.dungeon.persistence import DungeonStore

        conn = _mem_conn()
        store = DungeonStore(conn)

        _exporter, _provider, real_tracer = _otel_in_memory()
        original_tracer_fn = _spans_module.tracer
        _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
        try:
            req = self._build_request()
            with pytest.raises(NotImplementedError):
                materialize(req, graph=None, bundle=None, palette=None, persistence=store)
        finally:
            _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    def test_parent_span_opens_before_any_stage(self) -> None:
        """dungeon.materialize parent span must be emitted even though all
        stages raise NotImplementedError."""
        import sidequest.telemetry.spans as _spans_module
        from sidequest.dungeon.materializer import materialize
        from sidequest.dungeon.persistence import DungeonStore
        from sidequest.telemetry.spans.dungeon_materialize import SPAN_DUNGEON_MATERIALIZE

        conn = _mem_conn()
        store = DungeonStore(conn)

        exporter, _provider, real_tracer = _otel_in_memory()
        original_tracer_fn = _spans_module.tracer
        _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
        try:
            req = self._build_request()
            with pytest.raises(NotImplementedError):
                materialize(req, graph=None, bundle=None, palette=None, persistence=store)
        finally:
            _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

        finished = exporter.get_finished_spans()
        parent_spans = [s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE]
        assert parent_spans, (
            f"dungeon.materialize parent span not emitted — "
            f"got span names: {[s.name for s in finished]}"
        )

    def test_five_stage_spans_emitted_in_order_nested_under_parent(self) -> None:
        """Run materialize with a patched stage executor so all five stages
        run (no early-exit on NotImplementedError). Assert:
          - all five child spans are emitted
          - each child's parent is the dungeon.materialize parent span
          - spans appear in the order: design, fill, curate, attach, commit
        """
        import sidequest.telemetry.spans as _spans_module
        from sidequest.dungeon.materializer import (
            materialize,
        )
        from sidequest.dungeon.persistence import DungeonStore
        from sidequest.telemetry.spans.dungeon_materialize import (
            SPAN_DUNGEON_MATERIALIZE,
            SPAN_DUNGEON_MATERIALIZE_ATTACH,
            SPAN_DUNGEON_MATERIALIZE_COMMIT,
            SPAN_DUNGEON_MATERIALIZE_CURATE,
            SPAN_DUNGEON_MATERIALIZE_DESIGN,
            SPAN_DUNGEON_MATERIALIZE_FILL,
        )

        conn = _mem_conn()
        store = DungeonStore(conn)

        exporter, _provider, real_tracer = _otel_in_memory()
        original_tracer_fn = _spans_module.tracer
        _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]

        # Monkey-patch all five stages to no-ops so all five spans fire
        import sidequest.dungeon.materializer as _mat_module  # noqa: PLC0415

        original_design = _mat_module._stage_design
        original_fill = _mat_module._stage_fill
        original_curate = _mat_module._stage_curate
        original_attach = _mat_module._stage_attach
        original_commit = _mat_module._stage_commit

        def _noop(*args: object, **kwargs: object) -> None:
            pass

        _mat_module._stage_design = _noop  # type: ignore[assignment]
        _mat_module._stage_fill = _noop  # type: ignore[assignment]
        _mat_module._stage_curate = _noop  # type: ignore[assignment]
        _mat_module._stage_attach = _noop  # type: ignore[assignment]
        _mat_module._stage_commit = _noop  # type: ignore[assignment]
        try:
            req = self._build_request()
            materialize(req, graph=None, bundle=None, palette=None, persistence=store)
        finally:
            _mat_module._stage_design = original_design  # type: ignore[assignment]
            _mat_module._stage_fill = original_fill  # type: ignore[assignment]
            _mat_module._stage_curate = original_curate  # type: ignore[assignment]
            _mat_module._stage_attach = original_attach  # type: ignore[assignment]
            _mat_module._stage_commit = original_commit  # type: ignore[assignment]
            _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

        finished = exporter.get_finished_spans()
        span_names = [s.name for s in finished]

        # Parent span must be present
        parent_spans = [s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE]
        assert parent_spans, f"dungeon.materialize parent span missing; got: {span_names}"
        parent_span = parent_spans[0]

        # All five child spans must be present
        expected_children = [
            SPAN_DUNGEON_MATERIALIZE_DESIGN,
            SPAN_DUNGEON_MATERIALIZE_FILL,
            SPAN_DUNGEON_MATERIALIZE_CURATE,
            SPAN_DUNGEON_MATERIALIZE_ATTACH,
            SPAN_DUNGEON_MATERIALIZE_COMMIT,
        ]
        for child_name in expected_children:
            child_spans = [s for s in finished if s.name == child_name]
            assert child_spans, (
                f"Stage span {child_name!r} not emitted. "
                f"All span names: {span_names}"
            )

        # Each child's parent must be the dungeon.materialize parent span
        parent_context_span_id = parent_span.context.span_id
        for child_name in expected_children:
            child_span = next(s for s in finished if s.name == child_name)
            assert child_span.parent is not None, (
                f"{child_name!r} has no parent — it must be nested under "
                f"dungeon.materialize"
            )
            assert child_span.parent.span_id == parent_context_span_id, (
                f"{child_name!r}.parent.span_id != dungeon.materialize span_id — "
                f"the stage span is not nested under the parent"
            )

        # Children must appear in order: design, fill, curate, attach, commit
        # OTEL SimpleSpanProcessor exports spans as they END (LIFO inside
        # nested with-blocks). For sequential (non-nested) child spans, the
        # first child to start is the first to end.
        # We assert ordering by start time (monotonic).
        child_spans_in_order = [
            next(s for s in finished if s.name == cn) for cn in expected_children
        ]
        start_times = [s.start_time for s in child_spans_in_order]
        assert start_times == sorted(start_times), (
            "Stage spans must start in order: design, fill, curate, attach, commit. "
            f"Start times: {list(zip(expected_children, start_times, strict=True))}"
        )


# ---------------------------------------------------------------------------
# Test 3: wiring — public exports reachable from sidequest.dungeon
# ---------------------------------------------------------------------------


def test_materializer_exports_reachable_from_dungeon_package() -> None:
    """MaterializationRequest and materialize must be importable from
    sidequest.dungeon (the package __init__.py must export them)."""
    from sidequest.dungeon import MaterializationRequest, materialize  # type: ignore[attr-defined]

    assert callable(materialize)
    assert MaterializationRequest is not None


# ---------------------------------------------------------------------------
# Test 4: routing completeness gate (no new unrouted constants)
# ---------------------------------------------------------------------------


def test_dungeon_materialize_spans_registered_and_routed() -> None:
    """Every dungeon.materialize.* and frontier.expand constant must be in
    SPAN_ROUTES — the routing-completeness gate must not fail on our additions."""
    from sidequest.telemetry.spans import SPAN_ROUTES
    from sidequest.telemetry.spans.dungeon_materialize import (
        SPAN_DUNGEON_MATERIALIZE,
        SPAN_DUNGEON_MATERIALIZE_ATTACH,
        SPAN_DUNGEON_MATERIALIZE_COMMIT,
        SPAN_DUNGEON_MATERIALIZE_CURATE,
        SPAN_DUNGEON_MATERIALIZE_DESIGN,
        SPAN_DUNGEON_MATERIALIZE_FILL,
        SPAN_FRONTIER_EXPAND,
    )

    for name in (
        SPAN_DUNGEON_MATERIALIZE,
        SPAN_DUNGEON_MATERIALIZE_DESIGN,
        SPAN_DUNGEON_MATERIALIZE_FILL,
        SPAN_DUNGEON_MATERIALIZE_CURATE,
        SPAN_DUNGEON_MATERIALIZE_ATTACH,
        SPAN_DUNGEON_MATERIALIZE_COMMIT,
        SPAN_FRONTIER_EXPAND,
    ):
        assert name in SPAN_ROUTES, (
            f"{name!r} not in SPAN_ROUTES — routing-completeness gate will fail"
        )
