"""Beneath Sünden Plan 7 session-integration — pure seed builders.

Production analog of the test seeding helpers (``_make_seed_graph`` /
``_seed_graph_themed`` / ``MaterializationRequest_build``). No I/O: builds
the entrance ``RegionGraph`` and the expansion-1 ``MaterializationRequest``
the bootstrap feeds to the merged ``materialize`` pipeline, which commits
the entrance as Expansion 0 (Seed = Expansion 0 contract) and the generated
expansion 1.

Entrance theme = the shallowest depth-band palette theme (spec §13
decision 2). Deterministic (ties broken by theme id) — No Silent Fallbacks.
"""

from __future__ import annotations

from typing import Any

from sidequest.dungeon.materializer import MaterializationRequest
from sidequest.dungeon.persistence import FrontierEdge
from sidequest.dungeon.region_graph import RegionGraph, RegionNode

__all__ = [
    "ENTRANCE_ID",
    "build_entrance_seed_graph",
    "build_expansion_one_request",
    "select_entrance_theme_id",
]

# The Seed=Expansion-0 fixed anchor id (matches
# lookahead_worker._ENTRANCE_ID and load_map(entrance_id=...)).
ENTRANCE_ID = "entrance"

# The surface entrance sits at depth 0.0 (frozen root, spec §7).
_ENTRANCE_DEPTH = 0.0


def select_entrance_theme_id(palette: Any) -> str:
    """The theme id for the surface entrance: the shallowest-band theme
    eligible at depth 0.0, deterministic by id.

    Raises loudly if no theme covers the surface — a beneath_sunden
    palette with no depth-0 theme is a real content gap, never papered
    over with a silent default (No Silent Fallbacks).
    """
    eligible = palette.themes_for_depth(_ENTRANCE_DEPTH)
    if not eligible:
        raise ValueError(
            "no theme covers the surface entrance (depth 0.0) in the "
            "loaded ThemePalette — beneath_sunden content gap; refusing "
            "to invent an entrance theme (No Silent Fallbacks)"
        )
    # Defensive: do not depend on themes_for_depth's iteration order — sort by
    # id here so selection is reproducible regardless of that contract.
    return sorted(theme.id for theme in eligible)[0]


def build_entrance_seed_graph(entrance_theme_id: str) -> RegionGraph:
    """A seed graph containing only the entrance node at expansion 0.

    ``depth_score`` is left ``None`` — the merged commit stage freezes it
    to 0.0 (Seed = Expansion 0); the bootstrap never assigns it (save-is-
    truth: never recompute a frozen score).
    """
    g = RegionGraph(entrance_id=ENTRANCE_ID)
    g.add_node(RegionNode(id=ENTRANCE_ID, expansion_id=0, theme=entrance_theme_id))
    return g


def build_expansion_one_request(
    *,
    campaign_seed: int,
    genre_slug: str = "",
    world_slug: str = "",
) -> MaterializationRequest:
    """The first generated expansion's request (expansion_id == 1),
    pushing off the entrance.

    Mirrors the test ``MaterializationRequest_build`` shape: one frontier
    edge rooted at the entrance at spawn depth 0.0; attach to the
    entrance; burst 3.

    ``genre_slug`` / ``world_slug`` (Story 55-1) are threaded onto the
    returned request so the materializer's post-commit YAML emit can
    resolve ``<pack_root>/worlds/<world>``. Optional (default empty)
    for back-compat with bootstrap callers that don't need the YAML
    emit; production ``session_integration`` passes the live slugs.
    """
    fe = FrontierEdge(
        frontier_edge_id="seed_fe1",
        from_region_id=ENTRANCE_ID,
        heading="down",
        spawn_depth_score=_ENTRANCE_DEPTH,
    )
    return MaterializationRequest.build(
        campaign_seed=campaign_seed,
        expansion_id=1,
        frontier_edge=fe,
        frontier=[fe],
        attach_region_ids=[ENTRANCE_ID],
        heading="down",
        burst_magnitude=3,
        lookahead_breadth=1,
        genre_slug=genre_slug,
        world_slug=world_slug,
    )
