"""Beneath Sünden Plan 7 Tasks 1–2 — MaterializationRequest + pipeline coordinator.

``MaterializationRequest`` is a frozen, hashable value object carrying the
full specification for one dungeon expansion materialisation run.

``materialize()`` is the five-stage coordinator:
  design → fill → curate → attach → commit

At Task 1 each stage raises ``NotImplementedError`` so the skeleton's control
flow and OTEL span nesting are testable before any stage logic exists.
Task 2 implements ``_stage_design`` (theme palette depth-filtering + expansion
generation + report-pinned span attributes).
Later tasks fill the remaining stages in turn.

``frontier`` is accepted at construction time only for validation (confirming
that ``frontier_edge`` is a member of the live frontier); it is NOT stored as a
field so the hash is stable regardless of how the frontier list grows between
construction and use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace as _otel_trace

from sidequest.dungeon.persistence import DungeonStore, FrontierEdge
from sidequest.dungeon.region_graph import (
    Expansion,
    ExpansionGenerationError,
    GenerationReport,
    JaquaysConfig,
    RegionGraph,
    generate_expansion,
)
from sidequest.dungeon.themes import ThemePalette
from sidequest.telemetry.spans.dungeon_materialize import (
    dungeon_materialize_attach_span,
    dungeon_materialize_commit_span,
    dungeon_materialize_curate_span,
    dungeon_materialize_design_span,
    dungeon_materialize_fill_span,
    dungeon_materialize_span,
)

__all__ = [
    "MaterializationRequest",
    "materialize",
]

# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaterializationRequest:
    """Frozen, hashable specification for one materialisation run.

    Construct via :meth:`build` — it runs the loud validation and coerces
    ``attach_region_ids`` from a ``list[str]`` to a ``tuple[str, ...]`` so the
    object stays genuinely hashable (lists are unhashable). The bare
    constructor is the dataclass-generated one (positional fields, no
    validation) and is used only internally by :meth:`build`.

    Validation rules (enforced by :meth:`build`, no silent defaults):
    - ``expansion_id >= 1`` — expansion 0 is reserved for the seed/entrance
      per the Seed = Expansion 0 contract; negative values are always invalid.
    - ``frontier_edge`` must appear (by ``frontier_edge_id``) in the supplied
      ``frontier`` list — proves the edge exists before the request is built.
    - ``burst_magnitude >= 1`` — a zero or negative burst is incoherent.

    ``frontier`` is a :meth:`build`-only validation parameter: it is never
    stored as a field and does not affect equality or hashing.
    """

    campaign_seed: int
    expansion_id: int
    frontier_edge: FrontierEdge
    attach_region_ids: tuple[str, ...]
    heading: str
    burst_magnitude: int
    lookahead_breadth: int

    @classmethod
    def build(
        cls,
        *,
        campaign_seed: int,
        expansion_id: int,
        frontier_edge: FrontierEdge,
        attach_region_ids: list[str] | tuple[str, ...],
        heading: str,
        burst_magnitude: int,
        lookahead_breadth: int,
        frontier: list[FrontierEdge],
    ) -> MaterializationRequest:
        """Validate then build a frozen request. No silent defaults.

        ``frontier`` is consumed here only to prove ``frontier_edge`` is a live
        frontier member; it is never stored on the returned object.
        """
        if expansion_id < 1:
            raise ValueError(
                f"expansion_id must be >= 1 (expansion 0 is reserved for the "
                f"seed/entrance per the Seed=Expansion-0 contract); got {expansion_id!r}"
            )
        if burst_magnitude < 1:
            raise ValueError(
                f"burst_magnitude must be >= 1 (a zero/negative burst is "
                f"incoherent); got {burst_magnitude!r}"
            )
        frontier_ids = frozenset(fe.frontier_edge_id for fe in frontier)
        if frontier_edge.frontier_edge_id not in frontier_ids:
            raise ValueError(
                f"frontier_edge {frontier_edge.frontier_edge_id!r} is not in the "
                f"supplied frontier list (known ids: {sorted(frontier_ids)}). "
                f"A frontier_edge not present in the frontier cannot be expanded."
            )
        return cls(
            campaign_seed,
            expansion_id,
            frontier_edge,
            tuple(attach_region_ids),
            heading,
            burst_magnitude,
            lookahead_breadth,
        )


# ---------------------------------------------------------------------------
# Stage seams — each raises NotImplementedError at Task 1
# ---------------------------------------------------------------------------


def _stage_design(
    request: MaterializationRequest,
    *,
    graph: RegionGraph | None,
    palette: ThemePalette | None,
    span: _otel_trace.Span,
) -> tuple[Expansion, GenerationReport]:
    """Plan 7 Task 2: design stage — depth-filtered theme_pool + expansion generation.

    Depth-filters the palette via ``palette.themes_for_depth(depth_score)``
    (depth_score = ``request.frontier_edge.spawn_depth_score`` per the Seed=Expansion-0
    contract), calls ``generate_expansion``, and sets ``report.as_dict()`` as the
    exact span attribute set (byte-pinned GM-panel contract).

    Invariants (No Silent Fallbacks):
    - ``graph`` and ``palette`` must be real objects — ``None`` is rejected loudly.
    - Empty theme_pool after filtering → loud ``ValueError`` (generation meaningless).
    - ``ExpansionGenerationError`` propagates unchanged — no retry with smaller burst,
      no swallowing.  The span carries a failure attribute before re-raise so the GM
      panel can see the failure (lie-detector visibility).
    """
    if graph is None:
        raise ValueError(
            "_stage_design requires a real RegionGraph — "
            "graph=None is not valid (No Silent Fallbacks)"
        )
    if palette is None:
        raise ValueError(
            "_stage_design requires a real ThemePalette — "
            "palette=None is not valid (No Silent Fallbacks)"
        )
    depth_score: float = request.frontier_edge.spawn_depth_score

    eligible_themes = palette.themes_for_depth(depth_score)
    if not eligible_themes:
        raise ValueError(
            f"No themes eligible at depth_score={depth_score!r} — theme_pool would be "
            f"empty, which makes expansion generation meaningless. "
            f"Check the depth_band definitions in the theme palette."
        )
    theme_pool: list[str] = [t.id for t in eligible_themes]

    try:
        expansion, report = generate_expansion(
            graph=graph,
            campaign_seed=request.campaign_seed,
            expansion_id=request.expansion_id,
            attach_region_ids=list(request.attach_region_ids),
            theme_pool=theme_pool,
            config=JaquaysConfig(connection_burst=request.burst_magnitude),
        )
    except ExpansionGenerationError as exc:
        # Lie-detector: mark the span with the failure before re-raising so the
        # GM panel sees it.  No swallowing, no retry-with-smaller-burst.
        span.set_attribute("error", str(exc))
        span.set_attribute("failing", json.dumps(exc.failing, sort_keys=True))
        raise

    # Byte-pinned span attribute contract: exactly report.as_dict() keys/values.
    # invariants_passed is a dict — serialise as JSON so OTEL can carry it.
    report_dict = report.as_dict()
    for k, v in report_dict.items():
        if isinstance(v, dict):
            span.set_attribute(k, json.dumps(v, sort_keys=True))
        else:
            span.set_attribute(k, v)

    return expansion, report


def _stage_fill(request: MaterializationRequest, **kwargs: Any) -> Any:
    """Plan 7 Task 3: fill stage — creature/loot/set-piece manifest population."""
    raise NotImplementedError("_stage_fill not implemented until Plan 7 Task 3")


def _stage_curate(request: MaterializationRequest, **kwargs: Any) -> Any:
    """Plan 7 Task 4: curate stage — cookbook manifest curation + cookbook join."""
    raise NotImplementedError("_stage_curate not implemented until Plan 7 Task 4")


def _stage_attach(request: MaterializationRequest, **kwargs: Any) -> Any:
    """Plan 7 Task 5: attach stage — region-graph attach + depth scoring."""
    raise NotImplementedError("_stage_attach not implemented until Plan 7 Task 5")


def _stage_commit(request: MaterializationRequest, **kwargs: Any) -> Any:
    """Plan 7 Task 6: commit stage — persistence + frontier update."""
    raise NotImplementedError("_stage_commit not implemented until Plan 7 Task 6")


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


def materialize(
    request: MaterializationRequest,
    *,
    graph: RegionGraph | None,
    bundle: Any,
    palette: Any,
    persistence: DungeonStore,
) -> None:
    """Run the five-stage materialisation pipeline for one expansion.

    Opens a parent ``dungeon.materialize`` OTEL span; each of the five stage
    spans nests under it in order (design → fill → curate → attach → commit).

    At Task 1 every stage raises ``NotImplementedError`` — the skeleton exists
    so span nesting and control flow are testable before any stage logic lands.
    Later tasks (2–6) fill each stage in turn.

    Parameters
    ----------
    request:
        Frozen expansion specification. ``request.expansion_id`` must be >= 1.
    graph:
        The live ``RegionGraph`` (passed through to attach/commit stages).
    bundle:
        The cookbook bundle (passed through to curate stage).
    palette:
        The ``ThemePalette`` (passed through to design stage).
    persistence:
        The ``DungeonStore`` operating on the live save-DB connection
        (caller owns the transaction boundary, spec §7.5).
    """
    with dungeon_materialize_span(
        expansion_id=request.expansion_id,
        heading=request.heading,
        burst_magnitude=request.burst_magnitude,
    ):
        with dungeon_materialize_design_span(expansion_id=request.expansion_id) as design_span:
            _stage_design(request, graph=graph, palette=palette, span=design_span)

        with dungeon_materialize_fill_span(expansion_id=request.expansion_id):
            _stage_fill(request)

        with dungeon_materialize_curate_span(expansion_id=request.expansion_id):
            _stage_curate(request, bundle=bundle)

        with dungeon_materialize_attach_span(expansion_id=request.expansion_id):
            _stage_attach(request, graph=graph)

        with dungeon_materialize_commit_span(expansion_id=request.expansion_id):
            _stage_commit(request, graph=graph, persistence=persistence)
