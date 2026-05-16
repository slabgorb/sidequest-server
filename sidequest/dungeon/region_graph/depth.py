"""depth_score gradient (spec: Beneath Sünden §5, §10 step 3).

depth_score is the ONLY notion of depth: an abstract scalar attached to
each region AT ATTACH TIME and frozen into the save (never recomputed).
It is ≈ ordinary-route graph distance from the surface entrance,
deterministically jittered. "Level" survives only as a coarse,
approximate player-facing bucket — never an authoritative coordinate,
key, or container (spec decision rows 1, 2; §5).

Ordinary-route distance EXCLUDES hidden + shortcut edges: a secret
passage is not the ordinary route and a shortcut is a discovered bypass
(same rationale as invariants.py's `stitch` exclusion). Discovering a
shortcut later must NOT retroactively make a region shallower — scores
are frozen at attach.

Pure, deterministic, dependency-free. No OTEL / session wiring here —
Plan 7's materializer emits the `dungeon.materialize.attach` span from
DepthReport.as_dict() (same honest-deferral stance as Plan 2's
region_graph: see __init__.py docstring "later plans (3/4/5/7)").
"""

from __future__ import annotations

from dataclasses import dataclass

from sidequest.dungeon.region_graph.model import RegionGraph


@dataclass(frozen=True)
class DepthConfig:
    """Tunable depth knobs (spec §5 'All thresholds ... tunable in world config')."""

    depth_per_hop: float = 10.0
    jitter_max: float = 3.0
    # §12 decision: coarse shorthand = 3 ordinary hops per "level".
    # Deliberately NOT 1:1 (a 1-hop bucket would resurrect the rejected
    # discrete-floor concept — spec decision rows 1-2).
    bucket_size: float = 30.0

    def validate(self) -> None:
        if self.depth_per_hop <= 0.0:
            raise ValueError("depth_per_hop must be > 0")
        if self.jitter_max < 0.0:
            raise ValueError("jitter_max must be >= 0")
        if self.bucket_size < self.depth_per_hop:
            raise ValueError(
                "bucket_size must be >= depth_per_hop (a player-facing "
                "'level' must be coarser than a single hop, else it is a "
                "floor index — the explicitly-rejected concept)"
            )


def ordinary_route_dist(graph: RegionGraph) -> dict[str, int]:
    """BFS hop distance from the entrance over ORDINARY edges only —
    hidden (secret) and shortcut edges are excluded (a secret passage is
    not the ordinary route; a shortcut is a discovered bypass — same
    exclusion rationale as invariants.py's `stitch`).

    Raises loudly (CLAUDE.md: No Silent Fallbacks) if any region is not
    reachable from the entrance on the ordinary-route graph — depth must
    never silently default to 0 for an unreachable region.
    """
    skip = {
        i for i, e in enumerate(graph.edges) if e.hidden or e.shortcut
    }
    dist = graph.bfs_dist(graph.entrance_id, skip_edges=skip)
    missing = sorted(set(graph.nodes) - set(dist))
    if missing:
        raise ValueError(
            f"regions {missing} not reachable on the ordinary route "
            f"from {graph.entrance_id!r} (hidden/shortcut edges excluded); "
            f"cannot assign a depth_score"
        )
    return dist
