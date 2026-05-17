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

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from sidequest.dungeon.persistence import FrontierEdge  # noqa: E402
from sidequest.dungeon.region_graph import RegionGraph
from sidequest.dungeon.themes import ThemePalette

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
    under it in order.

    Note: After Task 2, design is implemented so the first NotImplementedError
    comes from _stage_fill (not design). The structural contract — parent span
    emitted, stages run in order, nesting correct — is unchanged.
    """

    def _build_request(self) -> Any:
        from sidequest.dungeon.materializer import MaterializationRequest
        from sidequest.dungeon.persistence import FrontierEdge

        fe = FrontierEdge(
            frontier_edge_id="fe1",
            from_region_id="entrance",
            heading="north",
            spawn_depth_score=15.0,
        )
        return MaterializationRequest.build(
            campaign_seed=7,
            expansion_id=1,
            frontier_edge=fe,
            frontier=[fe],
            attach_region_ids=["entrance"],
            heading="north",
            burst_magnitude=3,
            lookahead_breadth=2,
        )

    async def test_materialize_raises_not_implemented_error(self) -> None:
        """materialize() propagates NotImplementedError from the first
        still-deferred stage it hits. The boundary moves forward as each
        stage lands: design/fill/curate are implemented (Tasks 2–4), so
        the error now comes from _stage_attach (Task 5, still deferred).
        A real bundle + look-bound palette + reflecting curation client
        are required so curate runs end-to-end and the pipeline genuinely
        reaches the attach boundary."""
        import sidequest.telemetry.spans as _spans_module
        from sidequest.dungeon.materializer import materialize
        from sidequest.dungeon.persistence import DungeonStore

        conn = _mem_conn()
        store = DungeonStore(conn)

        bundle = _real_cookbook_bundle()
        _req, palette, _exp, _fill, _look = _curate_inputs(
            algorithm="prim", expansion_id=1, depth_score=0.5
        )

        _exporter, _provider, real_tracer = _otel_in_memory()
        original_tracer_fn = _spans_module.tracer
        _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
        try:
            req = self._build_request()
            graph = _make_seed_graph("entrance")
            with pytest.raises(NotImplementedError, match="attach"):
                await materialize(
                    req,
                    graph=graph,
                    bundle=bundle,
                    palette=palette,
                    persistence=store,
                    claude_client=_reflecting_claude_client(),
                )
        finally:
            _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    async def test_parent_span_opens_before_any_stage(self) -> None:
        """dungeon.materialize parent span must be emitted even when a
        still-deferred stage raises NotImplementedError (now attach)."""
        import sidequest.telemetry.spans as _spans_module
        from sidequest.dungeon.materializer import materialize
        from sidequest.dungeon.persistence import DungeonStore
        from sidequest.telemetry.spans.dungeon_materialize import SPAN_DUNGEON_MATERIALIZE

        conn = _mem_conn()
        store = DungeonStore(conn)

        bundle = _real_cookbook_bundle()
        _req, palette, _exp, _fill, _look = _curate_inputs(
            algorithm="prim", expansion_id=1, depth_score=0.5
        )

        exporter, _provider, real_tracer = _otel_in_memory()
        original_tracer_fn = _spans_module.tracer
        _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
        try:
            req = self._build_request()
            graph = _make_seed_graph("entrance")
            with pytest.raises(NotImplementedError, match="attach"):
                await materialize(
                    req,
                    graph=graph,
                    bundle=bundle,
                    palette=palette,
                    persistence=store,
                    claude_client=_reflecting_claude_client(),
                )
        finally:
            _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

        finished = exporter.get_finished_spans()
        parent_spans = [s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE]
        assert parent_spans, (
            f"dungeon.materialize parent span not emitted — "
            f"got span names: {[s.name for s in finished]}"
        )

    async def test_five_stage_spans_emitted_in_order_nested_under_parent(
        self,
    ) -> None:
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

        async def _async_noop(*args: object, **kwargs: object) -> None:
            # _stage_curate is `async def` (it awaits ClaudeClient.send);
            # the coordinator `await`s it. The no-op stub must therefore be
            # a coroutine function so `await _stage_curate(...)` is valid.
            # design/fill/attach/commit stay synchronous no-ops.
            return None

        def _design_noop(*args: object, **kwargs: object) -> tuple[object, object]:
            # The real _stage_design ALWAYS returns (Expansion,
            # GenerationReport) (Task 2 hard contract); the coordinator
            # unconditionally unpacks it. This stub honors that contract so
            # the test exercises the real unpack path while still no-op'ing
            # all stage logic — it only asserts span nesting/order.
            return (object(), object())

        _mat_module._stage_design = _design_noop  # type: ignore[assignment]
        _mat_module._stage_fill = _noop  # type: ignore[assignment]
        _mat_module._stage_curate = _async_noop  # type: ignore[assignment]
        _mat_module._stage_attach = _noop  # type: ignore[assignment]
        _mat_module._stage_commit = _noop  # type: ignore[assignment]
        try:
            req = self._build_request()
            await materialize(req, graph=None, bundle=None, palette=None, persistence=store)
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


# ---------------------------------------------------------------------------
# Task 2: Stage 1 design — generate_expansion + depth-filtered theme_pool
# ---------------------------------------------------------------------------


def _make_seed_graph(entrance_id: str = "entrance") -> RegionGraph:
    """A seed graph (only the entrance node) suitable for generate_expansion
    in seed-mode (is_seed=True path in the generator)."""
    from sidequest.dungeon.region_graph import RegionGraph, RegionNode

    g = RegionGraph(entrance_id=entrance_id)
    g.add_node(RegionNode(id=entrance_id, expansion_id=0, theme="tomb"))
    return g


def _make_theme_palette_two_themes(
    *,
    deep_theme_id: str = "deep_crypt",
    shallow_theme_id: str = "entry_hall",
    depth_score_cutoff: float = 20.0,
) -> ThemePalette:
    """Build a minimal ThemePalette with two themes:
    - shallow_theme_id: eligible at depth_score < depth_score_cutoff (max=cutoff-1)
    - deep_theme_id:    eligible at all depths (min=0, max=None)
    Used to verify that themes_for_depth correctly filters by band.
    """
    from sidequest.dungeon.themes import (
        Adjacency,
        DepthBand,
        DungeonTheme,
        InteriorSpec,
        NarratorFlavor,
        ThemePalette,
    )

    def _make_theme(tid: str, min_d: float, max_d: float | None) -> DungeonTheme:
        return DungeonTheme(
            id=tid,
            display_name=tid.replace("_", " ").title(),
            generator_class="organic",
            interior=InteriorSpec(algorithm="cellular", braid_ratio=0.0),
            depth_band=DepthBand(min=min_d, max=max_d),
            narrator=NarratorFlavor(register="grave", flavor="dread whispers"),
            adjacency=Adjacency(),
        )

    # shallow theme: max = depth_score_cutoff - 1 (so depth_score >= cutoff is excluded)
    shallow = _make_theme(shallow_theme_id, 0.0, depth_score_cutoff - 1.0)
    # deep theme: unbounded (eligible at ALL depths)
    deep = _make_theme(deep_theme_id, 0.0, None)
    return ThemePalette(themes={shallow_theme_id: shallow, deep_theme_id: deep})


class TestStageDesign:
    """Task 2 tests:
    1. design returns (Expansion, GenerationReport); span attrs = report.as_dict() exactly.
    2. ExpansionGenerationError propagates loudly + span carries failure.
    3. theme_pool is depth-filtered — excluded theme absent from pool.
    """

    # ---- helpers ----

    def _make_request(
        self,
        *,
        expansion_id: int = 1,
        burst_magnitude: int = 3,
        depth_score: float = 15.0,
        attach_region_ids: list[str] | None = None,
    ) -> Any:
        from sidequest.dungeon.materializer import MaterializationRequest
        from sidequest.dungeon.persistence import FrontierEdge

        fe = FrontierEdge(
            frontier_edge_id="fe1",
            from_region_id="entrance",
            heading="north",
            spawn_depth_score=depth_score,
        )
        return MaterializationRequest.build(
            campaign_seed=42,
            expansion_id=expansion_id,
            frontier_edge=fe,
            frontier=[fe],
            attach_region_ids=attach_region_ids or ["entrance"],
            heading="north",
            burst_magnitude=burst_magnitude,
            lookahead_breadth=2,
        )

    def _setup_otel(self):
        import sidequest.telemetry.spans as _spans_module

        exporter, provider, real_tracer = _otel_in_memory()
        original_tracer_fn = _spans_module.tracer
        _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
        return exporter, original_tracer_fn, _spans_module

    def _teardown_otel(self, _spans_module: Any, original_tracer_fn: Any) -> None:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    # ---- Test 1: design returns (Expansion, GenerationReport); span attrs = report.as_dict() ----

    def test_design_stage_returns_expansion_and_report(self) -> None:
        """_stage_design returns (Expansion, GenerationReport); the design span's
        attributes equal report.as_dict() exactly (key-set pinned)."""
        import json

        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.region_graph.invariants import GenerationReport
        from sidequest.telemetry.spans import SPAN_ROUTES
        from sidequest.telemetry.spans.dungeon_materialize import (
            SPAN_DUNGEON_MATERIALIZE_DESIGN,
            dungeon_materialize_design_span,
        )

        palette = _make_theme_palette_two_themes(depth_score_cutoff=20.0)
        graph = _make_seed_graph()
        request = self._make_request(depth_score=15.0)

        exporter, original_tracer_fn, _spans_mod = self._setup_otel()
        try:
            with dungeon_materialize_design_span(expansion_id=request.expansion_id) as span:
                result = _mat_module._stage_design(request, graph=graph, palette=palette, span=span)
        finally:
            self._teardown_otel(_spans_mod, original_tracer_fn)

        expansion, report = result
        assert isinstance(report, GenerationReport), (
            f"second element must be GenerationReport; got {type(report)}"
        )

        # Span attribute key-set must equal report.as_dict() keys exactly
        finished = exporter.get_finished_spans()
        design_spans = [s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE_DESIGN]
        assert design_spans, "dungeon.materialize.design span not emitted"
        span_attrs = dict(design_spans[0].attributes or {})
        report_dict = report.as_dict()

        assert set(span_attrs.keys()) == set(report_dict.keys()), (
            f"Span attribute key-set mismatch.\n"
            f"  Span keys: {sorted(span_attrs)}\n"
            f"  report.as_dict() keys: {sorted(report_dict)}"
        )
        # Check each value (invariants_passed is stored as JSON string on the span)
        for k, v in report_dict.items():
            if k == "invariants_passed":
                assert span_attrs[k] == json.dumps(v, sort_keys=True), (
                    f"span[{k!r}] = {span_attrs[k]!r}, expected JSON of {v!r}"
                )
            else:
                assert span_attrs[k] == v, (
                    f"span[{k!r}] = {span_attrs[k]!r}, expected {v!r}"
                )

        # Success path: the routed extract's failure markers read None
        # (graceful-get idiom — harmless; no error/failing attrs were set).
        route = SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_DESIGN]
        fields = route.extract(design_spans[0])  # type: ignore[arg-type]
        assert fields.get("error") is None
        assert fields.get("failing") is None

    # ---- Test 2: ExpansionGenerationError propagates + span carries failure ----

    def test_design_stage_propagates_expansion_generation_error_with_span(self) -> None:
        """When generate_expansion raises ExpansionGenerationError, _stage_design
        must NOT swallow it — it sets a failure attribute on the span and re-raises.

        Also asserts the *routed* SPAN_ROUTES extract surfaces error/failing — the
        raw span attributes are not what the GM panel renders; the typed
        state_transition event built by the route's extract is. The lie-detector
        is only honest if the route propagates the failure markers."""
        import json

        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.region_graph import ExpansionGenerationError
        from sidequest.telemetry.spans import SPAN_ROUTES
        from sidequest.telemetry.spans.dungeon_materialize import (
            SPAN_DUNGEON_MATERIALIZE_DESIGN,
            dungeon_materialize_design_span,
        )

        palette = _make_theme_palette_two_themes(depth_score_cutoff=20.0)
        graph = _make_seed_graph()
        request = self._make_request(depth_score=15.0)

        forced_error = ExpansionGenerationError(
            expansion_id=request.expansion_id,
            attempts=64,
            failing=["two_independent_entries"],
        )

        exporter, original_tracer_fn, _spans_mod = self._setup_otel()
        # Patch at the materializer's imported name (not the origin module).
        original_gen = _mat_module.generate_expansion

        def _always_fail(**kwargs: Any) -> Any:
            raise forced_error

        _mat_module.generate_expansion = _always_fail  # type: ignore[assignment]
        try:
            with pytest.raises(ExpansionGenerationError) as exc_info, dungeon_materialize_design_span(expansion_id=request.expansion_id) as span:
                _mat_module._stage_design(request, graph=graph, palette=palette, span=span)
        finally:
            _mat_module.generate_expansion = original_gen  # type: ignore[assignment]
            self._teardown_otel(_spans_mod, original_tracer_fn)

        # The error must be the exact instance (not a wrapped/swallowed one)
        assert exc_info.value is forced_error, (
            "ExpansionGenerationError must propagate unchanged — not swallowed or re-wrapped"
        )

        # The design span must carry a failure attribute (lie-detector visibility)
        finished = exporter.get_finished_spans()
        design_spans = [s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE_DESIGN]
        assert design_spans, "dungeon.materialize.design span must be emitted even on failure"
        finished_span = design_spans[0]
        span_attrs = dict(finished_span.attributes or {})
        assert "error" in span_attrs and "failing" in span_attrs, (
            f"Design span must carry both error and failing attributes on failure; "
            f"got attrs: {span_attrs}"
        )

        # Decisive: the ROUTED extract (what the GM panel actually renders) must
        # surface the failure markers, not just the raw span attributes.
        route = SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_DESIGN]
        fields = route.extract(finished_span)  # type: ignore[arg-type]
        assert fields.get("error") == str(forced_error), (
            f"Routed extract must surface 'error' on the failure path "
            f"(GM-panel lie-detector); got fields: {fields}"
        )
        assert fields.get("failing") == json.dumps(
            ["two_independent_entries"], sort_keys=True
        ), (
            f"Routed extract must surface 'failing' on the failure path; "
            f"got fields: {fields}"
        )

    # ---- Test 3: theme_pool is depth-filtered ----

    def test_design_stage_depth_filters_theme_pool(self) -> None:
        """Themes whose depth_band excludes depth_score are absent from the
        theme_pool passed to generate_expansion."""
        import sidequest.dungeon.materializer as _mat_module
        from sidequest.telemetry.spans.dungeon_materialize import dungeon_materialize_design_span

        # depth_score = 25.0; shallow theme max = 19.0 (< 25.0) → excluded
        # deep theme max = None → always eligible
        DEPTH_SCORE = 25.0
        CUTOFF = 20.0
        SHALLOW_ID = "entry_hall"
        DEEP_ID = "deep_crypt"
        palette = _make_theme_palette_two_themes(
            deep_theme_id=DEEP_ID,
            shallow_theme_id=SHALLOW_ID,
            depth_score_cutoff=CUTOFF,
        )
        graph = _make_seed_graph()
        request = self._make_request(depth_score=DEPTH_SCORE)

        captured_theme_pool: list[list[str]] = []
        # Patch at the materializer's imported name so the spy intercepts the call.
        original_gen = _mat_module.generate_expansion

        def _capture_pool(**kwargs: Any) -> Any:
            captured_theme_pool.append(list(kwargs["theme_pool"]))
            return original_gen(**kwargs)

        exporter, original_tracer_fn, _spans_mod = self._setup_otel()
        _mat_module.generate_expansion = _capture_pool  # type: ignore[assignment]
        try:
            with dungeon_materialize_design_span(expansion_id=request.expansion_id) as span:
                _mat_module._stage_design(request, graph=graph, palette=palette, span=span)
        finally:
            _mat_module.generate_expansion = original_gen  # type: ignore[assignment]
            self._teardown_otel(_spans_mod, original_tracer_fn)

        assert captured_theme_pool, "generate_expansion was not called"
        pool = captured_theme_pool[0]
        assert SHALLOW_ID not in pool, (
            f"Shallow theme {SHALLOW_ID!r} (max depth {CUTOFF - 1}) must be "
            f"excluded from theme_pool at depth_score {DEPTH_SCORE}; "
            f"got pool: {pool}"
        )
        assert DEEP_ID in pool, (
            f"Deep theme {DEEP_ID!r} (unbounded) must be in theme_pool; "
            f"got pool: {pool}"
        )

    # ---- Test 4: empty theme_pool raises loudly ----

    def test_design_stage_raises_if_no_themes_eligible_at_depth(self) -> None:
        """If themes_for_depth returns an empty list, _stage_design must raise
        loudly — not silently pass an empty pool to generate_expansion.
        Must raise ValueError specifically (not NotImplementedError)."""
        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.themes import (
            Adjacency,
            DepthBand,
            DungeonTheme,
            InteriorSpec,
            NarratorFlavor,
            ThemePalette,
        )
        from sidequest.telemetry.spans.dungeon_materialize import dungeon_materialize_design_span

        # Build a palette where the ONLY theme has max=5.0
        theme = DungeonTheme(
            id="shallow_only",
            display_name="Shallow Only",
            generator_class="organic",
            interior=InteriorSpec(algorithm="cellular"),
            depth_band=DepthBand(min=0.0, max=5.0),
            narrator=NarratorFlavor(register="grave", flavor="whispers"),
            adjacency=Adjacency(),
        )
        palette = ThemePalette(themes={"shallow_only": theme})
        graph = _make_seed_graph()
        # depth_score=50.0 — far beyond the theme's band
        request = self._make_request(depth_score=50.0)

        with pytest.raises(ValueError, match="[Tt]heme|depth|empty"), dungeon_materialize_design_span(expansion_id=request.expansion_id) as span:
            _mat_module._stage_design(request, graph=graph, palette=palette, span=span)


# ---------------------------------------------------------------------------
# Task 3: Stage 2 fill — generate_interior per region, theme-keyed, span
# ---------------------------------------------------------------------------


def _theme_for_class(
    tid: str,
    generator_class: str,
    *,
    braid_ratio: float = 0.0,
) -> Any:
    """Build a real DungeonTheme of the given generator_class with the
    spec §5.2 algorithm enforced by the model_validator. No mocking — real
    value objects only."""
    from sidequest.dungeon.themes import (
        Adjacency,
        DepthBand,
        DungeonTheme,
        InteriorSpec,
        NarratorFlavor,
    )

    class_algorithm = {
        "organic": "cellular",
        "labyrinthine": "depthfirst",
        "structured": "prim",
        "built": "roomcorridor",
    }
    return DungeonTheme(
        id=tid,
        display_name=tid.replace("_", " ").title(),
        generator_class=generator_class,
        interior=InteriorSpec(
            algorithm=class_algorithm[generator_class],
            braid_ratio=braid_ratio,
        ),
        depth_band=DepthBand(min=0.0, max=None),
        narrator=NarratorFlavor(register="grave", flavor="dread whispers"),
        adjacency=Adjacency(),
    )


def _make_request_task3(
    *,
    campaign_seed: int = 42,
    expansion_id: int = 1,
) -> Any:
    from sidequest.dungeon.materializer import MaterializationRequest
    from sidequest.dungeon.persistence import FrontierEdge

    fe = FrontierEdge(
        frontier_edge_id="fe1",
        from_region_id="entrance",
        heading="north",
        spawn_depth_score=15.0,
    )
    return MaterializationRequest.build(
        campaign_seed=campaign_seed,
        expansion_id=expansion_id,
        frontier_edge=fe,
        frontier=[fe],
        attach_region_ids=["entrance"],
        heading="north",
        burst_magnitude=3,
        lookahead_breadth=2,
    )


def _expansion_with_themes(*theme_ids: str, expansion_id: int = 1) -> Any:
    from sidequest.dungeon.region_graph import Expansion
    from sidequest.dungeon.region_graph.model import RegionNode

    nodes = [
        RegionNode(id=f"exp{expansion_id:03d}.r{i}", expansion_id=expansion_id, theme=tid)
        for i, tid in enumerate(theme_ids)
    ]
    return Expansion(expansion_id=expansion_id, new_nodes=nodes, new_edges=[])


def _setup_otel_task3() -> tuple[Any, Any, Any]:
    import sidequest.telemetry.spans as _spans_module

    exporter, _provider, real_tracer = _otel_in_memory()
    original_tracer_fn = _spans_module.tracer
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    return exporter, original_tracer_fn, _spans_module


class TestStageFill:
    """Task 3 tests:
    1. Every generator_class in the expansion maps to its spec §5.2 generator
       (cellular/depthfirst/prim/roomcorridor); an unknown algorithm raises
       loudly via generate_interior's OWN guard (not a re-implemented check).
    2. A labyrinth-trap theme fills with braid_ratio=0.0 (pristine); a non-trap
       maze theme with its palette braid_ratio. The span records the
       actually-applied ratio (lie detector: prove no silent default).
    """

    def test_each_generator_class_maps_to_its_spec_generator(self) -> None:
        """A region per generator_class fills with its §5.2 algorithm; the
        routed span payload records the algorithm actually used per region."""
        import json

        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.themes import ThemePalette
        from sidequest.telemetry.spans import SPAN_ROUTES
        from sidequest.telemetry.spans.dungeon_materialize import (
            SPAN_DUNGEON_MATERIALIZE_FILL,
            dungeon_materialize_fill_span,
        )

        themes = {
            "t_organic": _theme_for_class("t_organic", "organic"),
            "t_laby": _theme_for_class("t_laby", "labyrinthine"),
            "t_struct": _theme_for_class("t_struct", "structured"),
            "t_built": _theme_for_class("t_built", "built"),
        }
        palette = ThemePalette(themes=themes)
        expansion = _expansion_with_themes(
            "t_organic", "t_laby", "t_struct", "t_built"
        )
        request = _make_request_task3()

        exporter, original_tracer_fn, _spans_mod = _setup_otel_task3()
        try:
            with dungeon_materialize_fill_span(
                expansion_id=request.expansion_id
            ) as span:
                result = _mat_module._stage_fill(
                    request, expansion=expansion, palette=palette, span=span
                )
        finally:
            _spans_mod.tracer = original_tracer_fn

        # Result is an ordered mapping region_id -> RegionFill
        by_region = {rf.region_id: rf for rf in result.values()}
        assert by_region["exp001.r0"].algorithm == "cellular"
        assert by_region["exp001.r1"].algorithm == "depthfirst"
        assert by_region["exp001.r2"].algorithm == "prim"
        assert by_region["exp001.r3"].algorithm == "roomcorridor"

        # The routed span payload (what the GM panel renders) must surface
        # the per-region algorithm — not just the in-memory result.
        finished = exporter.get_finished_spans()
        fill_spans = [
            s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE_FILL
        ]
        assert fill_spans, "dungeon.materialize.fill span not emitted"
        route = SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_FILL]
        fields = route.extract(fill_spans[0])  # type: ignore[arg-type]
        regions_payload = json.loads(fields["regions"])
        algo_by_region = {r["region_id"]: r["algorithm"] for r in regions_payload}
        assert algo_by_region == {
            "exp001.r0": "cellular",
            "exp001.r1": "depthfirst",
            "exp001.r2": "prim",
            "exp001.r3": "roomcorridor",
        }

    def test_unknown_algorithm_raises_loudly_via_generators_own_guard(
        self,
    ) -> None:
        """Defense-in-depth: prove the materializer does NOT re-implement the
        algorithm check — it relies on generate_interior's OWN guard.

        In production this path is UNREACHABLE: InteriorSpec.algorithm's
        field_validator is the real gate at palette load, so a bad algorithm
        can never reach _stage_fill. This test deliberately bypasses that
        load-time gate (mutating the already-validated model in place) to
        force the materializer to dispatch a bad algorithm, then asserts
        generate_interior's own ValueError fires and the span carries the
        failure. It is not testing a real production failure mode — it pins
        the "reuse, don't re-implement the guard" contract."""
        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.themes import ThemePalette
        from sidequest.telemetry.spans import SPAN_ROUTES
        from sidequest.telemetry.spans.dungeon_materialize import (
            SPAN_DUNGEON_MATERIALIZE_FILL,
            dungeon_materialize_fill_span,
        )

        theme = _theme_for_class("t_organic", "organic")
        palette = ThemePalette(themes={"t_organic": theme})
        # The real production gate is InteriorSpec.algorithm's field_validator,
        # which runs at palette LOAD (not on assignment — Pydantic v2 does not
        # validate-on-assign by default, so a plain attribute set would equally
        # bypass it). Mutating the already-validated model here simulates an
        # algorithm that escaped that load-time gate, forcing the materializer
        # to dispatch it so generate_interior's OWN guard is the thing that
        # rejects it (we never re-implement that check).
        object.__setattr__(theme.interior, "algorithm", "no_such_algo")
        expansion = _expansion_with_themes("t_organic")
        request = _make_request_task3()

        exporter, original_tracer_fn, _spans_mod = _setup_otel_task3()
        try:
            with (
                pytest.raises(ValueError, match="unknown interior algorithm"),
                dungeon_materialize_fill_span(
                    expansion_id=request.expansion_id
                ) as span,
            ):
                _mat_module._stage_fill(
                    request, expansion=expansion, palette=palette, span=span
                )
        finally:
            _spans_mod.tracer = original_tracer_fn

        # The fill span must carry the failure marker (lie-detector visibility)
        finished = exporter.get_finished_spans()
        fill_spans = [
            s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE_FILL
        ]
        assert fill_spans, "fill span must be emitted even on failure"
        route = SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_FILL]
        fields = route.extract(fill_spans[0])  # type: ignore[arg-type]
        assert fields.get("error") is not None
        assert "no_such_algo" in fields["error"]

    def test_missing_theme_raises_loudly_with_span_failure(self) -> None:
        """A region whose theme is absent from the palette is a loud
        ValueError; the span carries the failure."""
        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.themes import ThemePalette
        from sidequest.telemetry.spans.dungeon_materialize import (
            SPAN_DUNGEON_MATERIALIZE_FILL,
            dungeon_materialize_fill_span,
        )

        palette = ThemePalette(themes={"t_organic": _theme_for_class("t_organic", "organic")})
        expansion = _expansion_with_themes("t_organic", "t_absent")
        request = _make_request_task3()

        exporter, original_tracer_fn, _spans_mod = _setup_otel_task3()
        try:
            with (
                pytest.raises(ValueError, match="t_absent"),
                dungeon_materialize_fill_span(
                    expansion_id=request.expansion_id
                ) as span,
            ):
                _mat_module._stage_fill(
                    request, expansion=expansion, palette=palette, span=span
                )
        finally:
            _spans_mod.tracer = original_tracer_fn

        finished = exporter.get_finished_spans()
        fill_spans = [
            s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE_FILL
        ]
        assert fill_spans
        attrs = dict(fill_spans[0].attributes or {})
        assert "error" in attrs

    def test_braid_ratio_applied_and_recorded_per_region(self) -> None:
        """A labyrinth-trap theme (braid_ratio=0.0) fills pristine; a non-trap
        maze theme fills with its palette braid_ratio (0.3). The span records
        the ACTUALLY-applied ratio per region (lie detector: not a default)."""
        import json

        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.themes import ThemePalette
        from sidequest.telemetry.spans import SPAN_ROUTES
        from sidequest.telemetry.spans.dungeon_materialize import (
            SPAN_DUNGEON_MATERIALIZE_FILL,
            dungeon_materialize_fill_span,
        )

        trap = _theme_for_class("t_trap", "labyrinthine", braid_ratio=0.0)
        maze = _theme_for_class("t_maze", "labyrinthine", braid_ratio=0.3)
        palette = ThemePalette(themes={"t_trap": trap, "t_maze": maze})
        expansion = _expansion_with_themes("t_trap", "t_maze")
        request = _make_request_task3()

        exporter, original_tracer_fn, _spans_mod = _setup_otel_task3()
        try:
            with dungeon_materialize_fill_span(
                expansion_id=request.expansion_id
            ) as span:
                result = _mat_module._stage_fill(
                    request, expansion=expansion, palette=palette, span=span
                )
        finally:
            _spans_mod.tracer = original_tracer_fn

        by_region = {rf.region_id: rf for rf in result.values()}
        assert by_region["exp001.r0"].braid_ratio == 0.0
        assert by_region["exp001.r1"].braid_ratio == 0.3

        finished = exporter.get_finished_spans()
        fill_spans = [
            s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE_FILL
        ]
        route = SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_FILL]
        fields = route.extract(fill_spans[0])  # type: ignore[arg-type]
        ratio_by_region = {
            r["region_id"]: r["braid_ratio"]
            for r in json.loads(fields["regions"])
        }
        assert ratio_by_region == {"exp001.r0": 0.0, "exp001.r1": 0.3}, (
            "span must record the actually-applied braid_ratio per region "
            "(0.0 trap, 0.3 maze) — proving no silent default"
        )

    def test_fill_is_deterministic_for_identical_inputs(self) -> None:
        """Identical (campaign_seed, expansion_id, region.id) ⇒ identical grid
        — the determinism contract for raw seed-reproducible fill."""
        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.themes import ThemePalette
        from sidequest.telemetry.spans.dungeon_materialize import (
            dungeon_materialize_fill_span,
        )

        palette = ThemePalette(
            themes={"t_organic": _theme_for_class("t_organic", "organic")}
        )

        def _run() -> Any:
            expansion = _expansion_with_themes("t_organic")
            request = _make_request_task3(campaign_seed=99, expansion_id=2)
            _exp, orig, mod = _setup_otel_task3()
            try:
                with dungeon_materialize_fill_span(
                    expansion_id=request.expansion_id
                ) as span:
                    return _mat_module._stage_fill(
                        request, expansion=expansion, palette=palette, span=span
                    )
            finally:
                mod.tracer = orig

        r1 = _run()
        r2 = _run()
        g1 = next(iter(r1.values())).grid
        g2 = next(iter(r2.values())).grid
        assert g1 == g2, "identical inputs must yield byte-identical grids"

    def test_roomcorridor_below_min_dim_raises_loudly(self) -> None:
        """A roomcorridor region below ROOMCORRIDOR_MIN_DIM is a loud
        ValueError naming the region, algorithm, dims and floor — no silent
        shrink/grow/skip (No Silent Fallbacks)."""
        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.themes import ThemePalette
        from sidequest.telemetry.spans.dungeon_materialize import (
            dungeon_materialize_fill_span,
        )

        # Force the default dims below the roomcorridor floor for this test.
        orig_w = _mat_module.DEFAULT_INTERIOR_WIDTH
        orig_h = _mat_module.DEFAULT_INTERIOR_HEIGHT
        _mat_module.DEFAULT_INTERIOR_WIDTH = 7  # type: ignore[misc]
        _mat_module.DEFAULT_INTERIOR_HEIGHT = 7  # type: ignore[misc]
        palette = ThemePalette(
            themes={"t_built": _theme_for_class("t_built", "built")}
        )
        expansion = _expansion_with_themes("t_built")
        request = _make_request_task3()
        try:
            with (
                pytest.raises(
                    ValueError,
                    match="roomcorridor.*floor|ROOMCORRIDOR_MIN_DIM|7",
                ),
                dungeon_materialize_fill_span(
                    expansion_id=request.expansion_id
                ) as span,
            ):
                _mat_module._stage_fill(
                    request, expansion=expansion, palette=palette, span=span
                )
        finally:
            _mat_module.DEFAULT_INTERIOR_WIDTH = orig_w  # type: ignore[misc]
            _mat_module.DEFAULT_INTERIOR_HEIGHT = orig_h  # type: ignore[misc]

    def test_degenerate_braid_fixed_point_seed_raises_loudly(self) -> None:
        """If a derived per-region interior seed equals the braid fixed point
        (0x5EED == 24301), fill must fail loudly rather than feed the braid
        sub-seed its degenerate fixed point."""
        import sidequest.dungeon.materializer as _mat_module
        from sidequest.dungeon.themes import ThemePalette
        from sidequest.telemetry.spans.dungeon_materialize import (
            dungeon_materialize_fill_span,
        )

        palette = ThemePalette(
            themes={"t_organic": _theme_for_class("t_organic", "organic")}
        )
        expansion = _expansion_with_themes("t_organic")
        request = _make_request_task3()

        # Force the seed mixer to return the fixed point for this region.
        orig_mixer = _mat_module._region_interior_seed
        _mat_module._region_interior_seed = (  # type: ignore[assignment]
            lambda *a, **k: _mat_module._BRAID_FIXED_POINT
        )
        try:
            with (
                pytest.raises(ValueError, match="24301|fixed point|0x5EED"),
                dungeon_materialize_fill_span(
                    expansion_id=request.expansion_id
                ) as span,
            ):
                _mat_module._stage_fill(
                    request, expansion=expansion, palette=palette, span=span
                )
        finally:
            _mat_module._region_interior_seed = orig_mixer  # type: ignore[assignment]

    async def test_fill_wired_into_coordinator(self) -> None:
        """Wiring test: materialize() reaches _stage_fill with real
        expansion+palette threaded from _stage_design, and the pipeline
        proceeds past fill AND curate (Tasks 3–4) to the still-deferred
        attach stage. (The deferral boundary moves forward as each stage
        lands — this test now needs a real bundle + look-bound palette +
        reflecting curation client so curate runs end-to-end.)"""
        import sidequest.telemetry.spans as _spans_module
        from sidequest.dungeon.materializer import materialize
        from sidequest.dungeon.persistence import DungeonStore

        conn = _mem_conn()
        store = DungeonStore(conn)

        bundle = _real_cookbook_bundle()
        _r, palette, _e, _f, _l = _curate_inputs(
            algorithm="prim", expansion_id=1, depth_score=0.5
        )

        _exporter, _provider, real_tracer = _otel_in_memory()
        original_tracer_fn = _spans_module.tracer
        _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
        try:
            req = _make_request_task3()
            graph = _make_seed_graph("entrance")
            # design + fill + curate now run; attach is still deferred.
            with pytest.raises(NotImplementedError, match="attach"):
                await materialize(
                    req,
                    graph=graph,
                    bundle=bundle,
                    palette=palette,
                    persistence=store,
                    claude_client=_reflecting_claude_client(),
                )
        finally:
            _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Task 4: Stage 3 curate — assemble_region + one-shot claude -p + CR→Edge
# ---------------------------------------------------------------------------

# The real shipped beneath_sunden world dir (Plan 5/8). Discovery mirrors
# tests/genre/test_beneath_sunden_world_load.py — parents[3] is the
# orchestrator root; the world dir is the load_cookbook input.
_BENEATH_SUNDEN_WORLD = (
    Path(__file__).resolve().parents[3]
    / "sidequest-content/genre_packs/caverns_and_claudes/worlds/beneath_sunden"
)


def _real_cookbook_bundle() -> Any:
    """Load the REAL beneath_sunden cookbook (no mocking the content)."""
    from sidequest.game.cookbook.loader import load_cookbook

    return load_cookbook(_BENEATH_SUNDEN_WORLD)


class _FakeProc:
    """Minimal asyncio.subprocess.Process stand-in (mirrors
    tests/agents/test_claude_client.py:FakeProcess) — the ONLY mocked
    seam: the claude -p subprocess. Real content, real cookbook, real
    EdgePool everywhere else."""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


def _curation_ok_payload(manifests: dict[str, Any]) -> bytes:
    """A well-formed curated payload: the claude -p JSON envelope whose
    `result` is the curator's per-region JSON verdict (keeps every
    wandering row + big_bad, lightly refining telegraphs). Mirrors the
    real --output-format json envelope (_parse_json_envelope reads
    `result`) AND the curate stage's contract that the verdict is keyed
    by region_id."""
    verdict = {
        region_id: {
            "race": m.race,
            "cr_band": m.cr_band,
            "wandering_table": [
                {**row, "telegraph": (row.get("telegraph") or "It is here.")}
                for row in m.wandering_table
            ],
            "big_bad": m.big_bad,
        }
        for region_id, m in manifests.items()
    }
    envelope = {"result": json.dumps(verdict), "usage": {"output_tokens": 10}}
    return json.dumps(envelope).encode()


def _fake_claude_client(spawn_proc: _FakeProc) -> Any:
    """A real ClaudeClient with the subprocess spawner injected (DI) so
    no real `claude` binary is ever launched (prompt: mock ONLY the
    subprocess)."""
    from sidequest.agents.claude_client import ClaudeClient

    async def _spawn(*_a: object, **_k: object) -> _FakeProc:
        return spawn_proc

    return ClaudeClient(timeout=5.0, spawn_fn=_spawn)


def _reflecting_claude_client() -> Any:
    """A real ClaudeClient whose injected spawner parses the curation
    prompt's ``INPUT:`` JSON (which the curate stage builds with the
    ACTUAL generated region ids + manifests) and echoes a well-formed
    per-region verdict back. Used by the wiring test where region ids are
    generated by the real design stage and not known in advance — still
    NEVER launches a real subprocess."""
    from sidequest.agents.claude_client import ClaudeClient

    async def _spawn(command: str, *args: object, **_k: object) -> _FakeProc:
        # claude_client builds args as [..., "-p", <prompt>, "--output-format", "json"]
        arglist = list(args)
        prompt = arglist[arglist.index("-p") + 1]
        assert isinstance(prompt, str)
        _, _, input_blob = prompt.partition("INPUT:\n")
        payload = json.loads(input_blob)
        verdict = {
            region_id: {
                "race": region["race"],
                "cr_band": region["cr_band"],
                "wandering_table": [
                    {**row, "telegraph": (row.get("telegraph") or "It is here.")}
                    for row in region["wandering_table"]
                ],
                "big_bad": region["big_bad"],
            }
            for region_id, region in payload.items()
        }
        envelope = {"result": json.dumps(verdict), "usage": {"output_tokens": 7}}
        return _FakeProc(json.dumps(envelope).encode())

    return ClaudeClient(timeout=5.0, spawn_fn=_spawn)


def _theme_bound_to_look(theme_id: str, algorithm: str) -> Any:
    """A real DungeonTheme whose interior.algorithm is the join key onto
    a LookDef.generator_binding (the resolved look→theme seam)."""
    from sidequest.dungeon.themes import (
        Adjacency,
        DepthBand,
        DungeonTheme,
        InteriorSpec,
        NarratorFlavor,
    )

    algo_class = {
        "cellular": "organic",
        "depthfirst": "labyrinthine",
        "prim": "structured",
        "roomcorridor": "built",
    }
    return DungeonTheme(
        id=theme_id,
        display_name=theme_id.replace("_", " ").title(),
        generator_class=algo_class[algorithm],
        interior=InteriorSpec(algorithm=algorithm, braid_ratio=0.0),
        depth_band=DepthBand(min=0.0, max=None),
        narrator=NarratorFlavor(register="grave", flavor="dread"),
        adjacency=Adjacency(),
    )


def _curate_inputs(
    *,
    algorithm: str = "prim",
    expansion_id: int = 1,
    depth_score: float = 0.5,
) -> tuple[Any, Any, Any, Any, str]:
    """Build (request, palette, expansion, fill_result, look) bound to a
    real look. `prim` → look `delvehold` (race dwarf at depth 0.5 → band
    `mid`, which has a big_bad gate — every creature must Edge-translate).
    """
    from sidequest.dungeon.materializer import MaterializationRequest, RegionFill
    from sidequest.dungeon.persistence import FrontierEdge
    from sidequest.dungeon.region_graph import Expansion
    from sidequest.dungeon.region_graph.model import RegionNode
    from sidequest.dungeon.themes import ThemePalette

    theme_id = f"t_{algorithm}"
    palette = ThemePalette(themes={theme_id: _theme_bound_to_look(theme_id, algorithm)})
    rid = f"exp{expansion_id:03d}.r0"
    nodes = [RegionNode(id=rid, expansion_id=expansion_id, theme=theme_id)]
    expansion = Expansion(expansion_id=expansion_id, new_nodes=nodes, new_edges=[])
    fe = FrontierEdge(
        frontier_edge_id="fe1",
        from_region_id="entrance",
        heading="north",
        spawn_depth_score=depth_score,
    )
    request = MaterializationRequest.build(
        campaign_seed=7,
        expansion_id=expansion_id,
        frontier_edge=fe,
        frontier=[fe],
        attach_region_ids=["entrance"],
        heading="north",
        burst_magnitude=3,
        lookahead_breadth=2,
    )
    fill_result = {
        rid: RegionFill(
            region_id=rid,
            algorithm=algorithm,
            width=49,
            height=49,
            braid_ratio=0.0,
            grid=[[0]],
        )
    }
    # `prim` is the generator_binding of look `delvehold` in the real
    # beneath_sunden cookbook (looks.yaml).
    look = "delvehold"
    return request, palette, expansion, fill_result, look


class TestStageCurate:
    """Task 4 tests (the 3 plan bullets):
    1. assemble_region called with EXACTLY the named signal kwargs (int→str
       at the seam); manifest deterministic for identical inputs (up to the
       curation seam).
    2. A curation subprocess failure raises loudly + aborts; the span
       records curated=false + a reason; raw manifest is NOT shipped
       stamped curated.
    3. Every corpus creature crossing the seam emerges with an EdgePool —
       no raw cr/hp leaks into the curate-stage output.
    """

    async def test_assemble_region_called_with_exact_signal_kwargs_and_is_deterministic(
        self,
    ) -> None:
        import sidequest.dungeon.materializer as _mat
        from sidequest.telemetry.spans.dungeon_materialize import (
            dungeon_materialize_curate_span,
        )

        bundle = _real_cookbook_bundle()
        request, palette, expansion, fill_result, look = _curate_inputs(
            algorithm="prim", expansion_id=4, depth_score=0.5
        )
        rid0 = expansion.new_nodes[0].id

        # Spy on assemble_region at the materializer's imported name.
        calls: list[dict] = []
        original = _mat.assemble_region

        def _spy(bundle_arg: Any, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return original(bundle_arg, **kwargs)

        # A success curation that echoes the manifest back unchanged.
        exporter, original_tracer_fn, _spans_mod = _setup_otel_task3()
        _mat.assemble_region = _spy  # type: ignore[assignment]
        try:
            with dungeon_materialize_curate_span(
                expansion_id=request.expansion_id
            ) as span:
                # Capture the pre-curation manifest the stage assembled so
                # the determinism assertion is up-to-the-seam.
                m0 = original(
                    bundle,
                    campaign_seed=str(request.campaign_seed),
                    expansion_id=str(request.expansion_id),
                    depth_score=0.5,
                    burst_magnitude=request.burst_magnitude,
                    look=look,
                    is_first_band_entry=True,
                )
                proc = _FakeProc(_curation_ok_payload({rid0: m0}))
                result = await _mat._stage_curate(
                    request,
                    bundle=bundle,
                    palette=palette,
                    expansion=expansion,
                    fill_result=fill_result,
                    is_first_band_entry=True,
                    claude_client=_fake_claude_client(proc),
                    span=span,
                )
        finally:
            _mat.assemble_region = original  # type: ignore[assignment]
            _spans_mod.tracer = original_tracer_fn

        assert calls, "assemble_region was not called by _stage_curate"
        kw = calls[0]
        # DIVERGENCE 1: campaign_seed / expansion_id are str at this seam.
        assert kw["campaign_seed"] == str(request.campaign_seed)
        assert kw["expansion_id"] == str(request.expansion_id)
        assert isinstance(kw["campaign_seed"], str)
        assert isinstance(kw["expansion_id"], str)
        # depth_score from the frontier edge; burst from the request.
        assert kw["depth_score"] == request.frontier_edge.spawn_depth_score
        assert kw["burst_magnitude"] == request.burst_magnitude
        # The look the stage passed to assemble_region is the one it
        # derived loudly from the region theme (theme=t_prim →
        # generator_binding 'prim' → cookbook look 'delvehold'). No
        # caller override exists; assert the derived value end-to-end.
        assert kw["look"] == "delvehold"
        assert result.region_look[rid0] == "delvehold"
        assert kw["is_first_band_entry"] is True

        # Pre-curation determinism: assemble_region is pure — identical
        # inputs ⇒ identical manifest (the contract holds UP TO the
        # curation seam).
        m_a = original(
            bundle,
            campaign_seed=str(request.campaign_seed),
            expansion_id=str(request.expansion_id),
            depth_score=request.frontier_edge.spawn_depth_score,
            burst_magnitude=request.burst_magnitude,
            look=look,
            is_first_band_entry=True,
        )
        m_b = original(
            bundle,
            campaign_seed=str(request.campaign_seed),
            expansion_id=str(request.expansion_id),
            depth_score=request.frontier_edge.spawn_depth_score,
            burst_magnitude=request.burst_magnitude,
            look=look,
            is_first_band_entry=True,
        )
        assert m_a.model_dump() == m_b.model_dump(), (
            "assemble_region must be deterministic for identical inputs "
            "(pre-curation determinism contract, up to the curation seam)"
        )

    async def test_curation_subprocess_failure_raises_and_records_curated_false(
        self,
    ) -> None:
        import sidequest.dungeon.materializer as _mat
        from sidequest.telemetry.spans import SPAN_ROUTES
        from sidequest.telemetry.spans.dungeon_materialize import (
            SPAN_DUNGEON_MATERIALIZE_CURATE,
            dungeon_materialize_curate_span,
        )

        bundle = _real_cookbook_bundle()
        request, palette, expansion, fill_result, _look = _curate_inputs()

        # Non-zero exit, empty stdout — a hard subprocess failure.
        proc = _FakeProc(b"", b"boom", returncode=1)

        exporter, original_tracer_fn, _spans_mod = _setup_otel_task3()
        try:
            with pytest.raises(Exception) as exc_info, dungeon_materialize_curate_span(  # noqa: PT011
                expansion_id=request.expansion_id
            ) as span:
                await _mat._stage_curate(
                    request,
                    bundle=bundle,
                    palette=palette,
                    expansion=expansion,
                    fill_result=fill_result,
                    is_first_band_entry=True,
                    claude_client=_fake_claude_client(proc),
                    span=span,
                )
        finally:
            _spans_mod.tracer = original_tracer_fn

        # Loud: NOT NotImplementedError, NOT a swallowed pass.
        assert not isinstance(exc_info.value, NotImplementedError)

        finished = exporter.get_finished_spans()
        curate_spans = [
            s for s in finished if s.name == SPAN_DUNGEON_MATERIALIZE_CURATE
        ]
        assert curate_spans, "curate span must be emitted even on failure"
        route = SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_CURATE]
        fields = route.extract(curate_spans[0])  # type: ignore[arg-type]
        # The routed extract (what the GM panel renders) must show the
        # failure — set-but-not-routed is the Task-2 defect lesson.
        assert fields.get("curated") is False, (
            f"routed extract must surface curated=false on failure; got {fields}"
        )
        assert fields.get("reason"), (
            f"routed extract must surface a specific failure reason; got {fields}"
        )
        # Decisive: the raw manifest was NOT shipped stamped curated.
        assert fields.get("curated") is not True

    async def test_every_corpus_creature_emerges_with_edge_no_raw_cr_hp(
        self,
    ) -> None:
        import sidequest.dungeon.materializer as _mat
        from sidequest.game.creature_core import EdgePool
        from sidequest.telemetry.spans.dungeon_materialize import (
            dungeon_materialize_curate_span,
        )

        bundle = _real_cookbook_bundle()
        # depth 0.5 → band `mid`; `mid` is in
        # affinities.big_bad_gate.on_first_band_entry, and the fixture
        # passes is_first_band_entry=True, so the manifest is GUARANTEED a
        # big_bad — both the wandering table AND a big_bad cross the
        # CR→Edge seam (verified: race=dwarf yields big_bad 'Wight').
        request, palette, expansion, fill_result, look = _curate_inputs(
            depth_score=0.5
        )
        rid0 = expansion.new_nodes[0].id

        from sidequest.dungeon.materializer import assemble_region

        m0 = assemble_region(
            bundle,
            campaign_seed=str(request.campaign_seed),
            expansion_id=str(request.expansion_id),
            depth_score=0.5,
            burst_magnitude=request.burst_magnitude,
            look=look,
            is_first_band_entry=True,
        )
        assert m0.wandering_table, "fixture must exercise a non-empty wandering table"
        assert m0.big_bad is not None, (
            "band 'mid' + is_first_band_entry=True must yield a big_bad in "
            "the real cookbook so the big_bad CR→Edge path is provably "
            "exercised (fixture/affinities contract)"
        )

        proc = _FakeProc(_curation_ok_payload({rid0: m0}))

        _exporter, original_tracer_fn, _spans_mod = _setup_otel_task3()
        try:
            with dungeon_materialize_curate_span(
                expansion_id=request.expansion_id
            ) as span:
                result = await _mat._stage_curate(
                    request,
                    bundle=bundle,
                    palette=palette,
                    expansion=expansion,
                    fill_result=fill_result,
                    is_first_band_entry=True,
                    claude_client=_fake_claude_client(proc),
                    span=span,
                )
        finally:
            _spans_mod.tracer = original_tracer_fn

        # I2: the look is derived from the theme inside the stage — assert
        # the resolved value end-to-end (no caller override exists).
        assert result.region_look[rid0] == "delvehold"

        creatures = result.creatures_for_region(rid0)
        assert creatures, "curated region must carry its creatures"
        for c in creatures:
            assert isinstance(c.edge, EdgePool), (
                f"every corpus creature must emerge with an EdgePool; "
                f"{c.name!r} has {type(c.edge)}"
            )
            assert c.edge.max >= 1 and c.edge.current == c.edge.max
            # No raw cr/hp may leak onto the curated creature object.
            assert not hasattr(c, "cr"), f"{c.name!r} leaked raw cr"
            assert not hasattr(c, "hp"), f"{c.name!r} leaked raw hp"

        # The big_bad (band `mid` gate, GUARANTEED above) must ALSO be
        # Edge-translated — decisively, not conditionally.
        bb = result.big_bad_for_region(rid0)
        assert bb is not None, (
            "band 'mid' fixture must yield a big_bad so the big_bad "
            "CR→Edge path is provably exercised"
        )
        assert isinstance(bb.edge, EdgePool)
        assert bb.edge.max >= 1 and bb.edge.current == bb.edge.max
        assert not hasattr(bb, "cr")
        assert not hasattr(bb, "hp")

    async def test_curate_wired_into_coordinator(self) -> None:
        """Wiring: materialize() reaches _stage_curate with real
        expansion+palette+bundle threaded from design/fill, runs the
        injected fake curation, and proceeds PAST curate to the
        still-deferred attach stage."""
        import sidequest.telemetry.spans as _spans_module
        from sidequest.dungeon.materializer import materialize
        from sidequest.dungeon.persistence import DungeonStore

        bundle = _real_cookbook_bundle()
        # A palette whose single theme binds to look `delvehold` (prim).
        request, palette, _expansion, _fill, _look = _curate_inputs(
            algorithm="prim", expansion_id=1, depth_score=0.5
        )
        # The coordinator runs the REAL _stage_design/_stage_fill, which
        # need the seed graph + a palette whose themes resolve at the
        # frontier depth. Reuse the design/fill helpers' palette but bind
        # its themes to a real look so the curate look-resolution passes.
        graph = _make_seed_graph("entrance")

        # The REAL _stage_design generates region ids dynamically, so the
        # curation verdict cannot be precomputed. The reflecting client
        # parses the curate stage's actual prompt and echoes a well-formed
        # per-region verdict — still never launching a real subprocess.
        reflecting = _reflecting_claude_client()

        conn = _mem_conn()
        store = DungeonStore(conn)
        _exporter, _provider, real_tracer = _otel_in_memory()
        original_tracer_fn = _spans_module.tracer
        _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
        try:
            # design + fill + curate now run; attach is still deferred.
            with pytest.raises(NotImplementedError, match="attach"):
                await materialize(
                    request,
                    graph=graph,
                    bundle=bundle,
                    palette=palette,
                    persistence=store,
                    claude_client=reflecting,
                )
        finally:
            _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]
