"""Beneath Sünden Plan 7 Task 1 — MaterializationRequest + pipeline skeleton.

``MaterializationRequest`` is a frozen, hashable value object carrying the
full specification for one dungeon expansion materialisation run.

``materialize()`` is the five-stage coordinator:
  design → fill → curate → attach → commit

At Task 1 each stage raises ``NotImplementedError`` so the skeleton's control
flow and OTEL span nesting are testable before any stage logic exists.
Later tasks fill each stage in turn.

``frontier`` is accepted at construction time only for validation (confirming
that ``frontier_edge`` is a member of the live frontier); it is NOT stored as a
field so the hash is stable regardless of how the frontier list grows between
construction and use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sidequest.dungeon.persistence import DungeonStore, FrontierEdge
from sidequest.dungeon.region_graph import RegionGraph
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


def _stage_design(request: MaterializationRequest, **kwargs: Any) -> Any:
    """Plan 7 Task 2: design stage — theme palette + node blueprint generation."""
    raise NotImplementedError("_stage_design not implemented until Plan 7 Task 2")


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
        with dungeon_materialize_design_span(expansion_id=request.expansion_id):
            _stage_design(request, palette=palette)

        with dungeon_materialize_fill_span(expansion_id=request.expansion_id):
            _stage_fill(request)

        with dungeon_materialize_curate_span(expansion_id=request.expansion_id):
            _stage_curate(request, bundle=bundle)

        with dungeon_materialize_attach_span(expansion_id=request.expansion_id):
            _stage_attach(request, graph=graph)

        with dungeon_materialize_commit_span(expansion_id=request.expansion_id):
            _stage_commit(request, graph=graph, persistence=persistence)
