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

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace as _otel_trace

from sidequest.dungeon.interiors import generate_interior
from sidequest.dungeon.interiors.grid import Grid
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
    "RegionFill",
    "materialize",
]

# ---------------------------------------------------------------------------
# §12-style tunable knobs (Plan 7 Task 3)
# ---------------------------------------------------------------------------
#
# The per-region interior grid size is NOT carried by RegionNode and spec §5.2
# does NOT specify interior dimensions (spec line 120: "All thresholds and
# burst knobs are tunable in the world config"; Plan 7 is server-only — no
# content edits). The materializer therefore OWNS this as a §12-style tunable
# default knob, reconciled at execution exactly like the plan's §12 ledger
# resolved its three other knobs.
#
# 49×49 is chosen deliberately: it is odd (the maze-maker carve generators in
# interiors/ work on odd coordinates with midpoint carving), generous enough
# that gen_roomcorridor (max_rooms=12, room_max=7) produces non-degenerate room
# placement rather than a sparse retry-loop dropout. A single clear default —
# NOT a per-theme framework, NOT speculative config plumbing.
DEFAULT_INTERIOR_WIDTH = 49
DEFAULT_INTERIOR_HEIGHT = 49

# gen_roomcorridor's OWN guard raises below 5×5 (loud — fine). The real SILENT
# risk is its room-placement retry loop yielding sparse/degenerate rooms at
# valid-but-small dims. We refuse roomcorridor regions below this generous
# floor (comfortably above the generator's own 5) and fail loudly rather than
# silently shrink/grow/skip (CLAUDE.md: No Silent Fallbacks). Other algorithms
# rely on generate_interior's own loud too-small guard — not re-implemented here.
ROOMCORRIDOR_MIN_DIM = 25

# generate_interior's braid sub-seed is `seed ^ 0x5EED`, which degenerates to 0
# at seed == 0x5EED (24301). We derive per-region seeds with a non-XOR blake2b
# mixer (the Plan-2/3 _subseed precedent), but additionally refuse the fixed
# point loudly if a derived seed ever lands exactly on it.
_BRAID_FIXED_POINT = 0x5EED  # == 24301


def _region_interior_seed(
    campaign_seed: int, expansion_id: int, region_id: str
) -> int:
    """Deterministic per-region interior seed.

    Mirrors ``region_graph.generator._subseed`` /
    ``setpiece_attach`` exactly: a pipe-delimited UTF-8 string →
    ``blake2b(digest_size=8)`` → big-endian int. blake2b, NOT XOR — the
    house mixer; we do not invent a new scheme. Identical
    ``(campaign_seed, expansion_id, region_id)`` ⇒ identical seed ⇒
    identical raw fill (the determinism contract).
    """
    digest = hashlib.blake2b(
        f"{campaign_seed}|{expansion_id}|{region_id}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big")


@dataclass(frozen=True, slots=True)
class RegionFill:
    """In-memory result of filling one region's interior (Plan 7 Task 3).

    A frozen, slotted value object — same idiom as Task 1's
    ``MaterializationRequest`` and ``FrontierEdge``. ADR-055
    ``rooms.yaml`` shape + ADR-096 mask sidecar serialization is the
    COMMIT stage's job (Task 6), not fill's: this is purely the
    intermediate per-region grid + the metadata the lie-detector span
    needs.

    Note: ``frozen`` blocks field reassignment but ``grid`` is a mutable
    list-of-lists; treat it as read-only by convention.
    """

    region_id: str
    algorithm: str
    width: int
    height: int
    braid_ratio: float
    grid: Grid

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


def _stage_fill(
    request: MaterializationRequest,
    *,
    expansion: Expansion | None,
    palette: ThemePalette | None,
    span: _otel_trace.Span,
) -> dict[str, RegionFill]:
    """Plan 7 Task 3: fill stage — generate each region's interior grid.

    For every region node in ``expansion``, resolves its theme's interior
    algorithm + params + braid_ratio from ``palette`` and calls
    ``interiors.generate_interior``. Returns a ``dict`` mapping
    ``region_id -> RegionFill``; iteration order = ``expansion.new_nodes``
    order (dict insertion-order is a Python 3.7+ language guarantee). The
    ADR-055 ``rooms.yaml`` shape / ADR-096 mask sidecar serialization is
    consumed at Task 6 (commit), NOT here.

    Invariants (No Silent Fallbacks):
    - ``expansion`` and ``palette`` must be real objects — ``None`` is
      rejected loudly.
    - A region whose ``theme`` id is absent from ``palette.themes`` →
      loud ``ValueError`` (no skip, no default theme).
    - A roomcorridor region below ``ROOMCORRIDOR_MIN_DIM`` → loud
      ``ValueError`` naming the region/algorithm/dims/floor (the silent
      degenerate-rooms risk; no shrink/grow/skip).
    - A derived per-region seed equal to the braid fixed point
      (``0x5EED`` == 24301) → loud ``ValueError`` (would feed the braid
      sub-seed its degenerate fixed point).
    - An unknown algorithm raises via ``generate_interior``'s OWN guard
      (not re-implemented here) — it propagates unchanged.

    Any failure sets ``error`` on the span before raising so the GM panel
    sees the fill failure (lie-detector visibility) — no swallowing.
    """
    if expansion is None:
        raise ValueError(
            "_stage_fill requires a real Expansion — "
            "expansion=None is not valid (No Silent Fallbacks)"
        )
    if palette is None:
        raise ValueError(
            "_stage_fill requires a real ThemePalette — "
            "palette=None is not valid (No Silent Fallbacks)"
        )

    fills: dict[str, RegionFill] = {}
    try:
        for node in expansion.new_nodes:
            if node.theme not in palette.themes:
                raise ValueError(
                    f"region {node.id!r} references theme {node.theme!r} which "
                    f"is absent from the palette (have: "
                    f"{sorted(palette.themes)}). No silent default theme."
                )
            theme = palette.themes[node.theme]
            algorithm = theme.interior.algorithm
            braid_ratio = theme.interior.braid_ratio
            params = theme.interior.params
            width = DEFAULT_INTERIOR_WIDTH
            height = DEFAULT_INTERIOR_HEIGHT

            # roomcorridor degenerate-rooms floor (carry-forward): loud, no
            # shrink/grow/skip. Other algorithms rely on generate_interior's
            # own loud too-small guard.
            if algorithm == "roomcorridor" and (
                width < ROOMCORRIDOR_MIN_DIM or height < ROOMCORRIDOR_MIN_DIM
            ):
                raise ValueError(
                    f"region {node.id!r} algorithm 'roomcorridor' dims "
                    f"{width}x{height} are below ROOMCORRIDOR_MIN_DIM="
                    f"{ROOMCORRIDOR_MIN_DIM} (the room-placement retry loop "
                    f"degenerates to sparse/empty rooms below this floor). "
                    f"No silent shrink/grow/skip."
                )

            seed = _region_interior_seed(
                request.campaign_seed, request.expansion_id, node.id
            )
            # Degenerate-seed guard (carry-forward): generate_interior's braid
            # sub-seed is seed ^ 0x5EED → 0 at the fixed point. Refuse it loudly.
            if seed == _BRAID_FIXED_POINT:
                raise ValueError(
                    f"region {node.id!r} derived interior seed equals the braid "
                    f"fixed point 0x5EED ({_BRAID_FIXED_POINT}); the braid "
                    f"sub-seed (seed ^ 0x5EED) would degenerate to 0. Refusing "
                    f"to feed the braid its fixed point (No Silent Fallbacks)."
                )

            grid = generate_interior(
                algorithm,
                width=width,
                height=height,
                seed=seed,
                braid_ratio=braid_ratio,
                params=params or None,
            )
            fills[node.id] = RegionFill(
                region_id=node.id,
                algorithm=algorithm,
                width=width,
                height=height,
                braid_ratio=braid_ratio,
                grid=grid,
            )
    except ValueError as exc:
        # Lie-detector: surface the fill failure on the span before re-raise
        # so the GM panel sees it. No swallowing, no retry, no skip.
        span.set_attribute("error", str(exc))
        raise

    # Lie-detector success payload: the ACTUALLY-applied algorithm + dims +
    # braid_ratio per region (proving braid_ratio was not silently defaulted).
    regions_payload = [
        {
            "region_id": rf.region_id,
            "algorithm": rf.algorithm,
            "width": rf.width,
            "height": rf.height,
            "braid_ratio": rf.braid_ratio,
        }
        for rf in fills.values()
    ]
    span.set_attribute("regions", json.dumps(regions_payload, sort_keys=True))
    span.set_attribute("region_count", len(fills))

    return fills


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
            expansion, _report = _stage_design(
                request, graph=graph, palette=palette, span=design_span
            )

        with dungeon_materialize_fill_span(expansion_id=request.expansion_id) as fill_span:
            _stage_fill(request, expansion=expansion, palette=palette, span=fill_span)

        with dungeon_materialize_curate_span(expansion_id=request.expansion_id):
            _stage_curate(request, bundle=bundle)

        with dungeon_materialize_attach_span(expansion_id=request.expansion_id):
            _stage_attach(request, graph=graph)

        with dungeon_materialize_commit_span(expansion_id=request.expansion_id):
            _stage_commit(request, graph=graph, persistence=persistence)
