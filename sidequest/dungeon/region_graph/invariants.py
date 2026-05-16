"""Jaquays invariant checker (spec §5.1) — exact, by counting + BFS.

Why no cycle enumeration: with the already-explored map connected
(maintained on every attach; seed expansion special-cased), contract
explored to a single vertex X. The new regions hang off X via the
stitch edges. A vertex X with k incident edges into an otherwise
acyclic structure contributes exactly k-1 independent fundamental
cycles, and every one of those cycles passes through X (= explored)
and through >= 1 new region. Hence:

    loops_into_explored == max(0, len(stitch_edges) - 1)

is exact and needs no DFS. All other invariants are degree / distinct
counts / BFS-distance deltas — also exact.

Hidden (secret) and shortcut cross-edges are EXCLUDED from `stitch`:
a secret passage is not a reliable independent entry, and a shortcut
is a discovered bypass — each already satisfies its own separate
§5.1 invariant and must not be double-counted toward the
"≥2 independent entries" floor. Consequence: with hidden/shortcut
cross-edges present, `loops_into_explored = max(0, len(stitch)-1)`
is a conservative LOWER BOUND, not strict equality (those edges add
real, uncounted loops). This is safe for a re-roll post-condition —
it can only cause an extra re-roll, never a false pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sidequest.dungeon.region_graph.config import JaquaysConfig
from sidequest.dungeon.region_graph.model import Expansion, RegionGraph

_INVARIANTS = (
    "two_independent_entries",
    "loops_into_explored",
    "mixed_kinds_with_hidden",
    "shortcut_collapses_distance",
    "no_single_entrance",
    "no_single_chokepoint",
)


@dataclass
class GenerationReport:
    """Span-ready data contract — Plan 7's materializer turns this into
    `dungeon.materialize.design` OTEL attributes."""

    expansion_id: int
    attempts: int = 1
    stitch_edges: int = 0
    loops_into_explored: int = 0
    hidden_edges: int = 0
    shortcut_edges: int = 0
    new_regions: int = 0
    invariants_passed: dict[str, bool] = field(default_factory=dict)

    def all_passed(self) -> bool:
        return bool(self.invariants_passed) and all(self.invariants_passed.values())

    def failing(self) -> list[str]:
        return [k for k, ok in self.invariants_passed.items() if not ok]

    def as_dict(self) -> dict:
        return {
            "expansion_id": self.expansion_id,
            "attempts": self.attempts,
            "stitch_edges": self.stitch_edges,
            "loops_into_explored": self.loops_into_explored,
            "hidden_edges": self.hidden_edges,
            "shortcut_edges": self.shortcut_edges,
            "new_regions": self.new_regions,
            "invariants_passed": dict(self.invariants_passed),
        }


def _post_attach_graph(explored: RegionGraph, exp: Expansion) -> RegionGraph:
    """A throwaway copy of explored with the expansion applied (for checks)."""
    g = RegionGraph(entrance_id=explored.entrance_id)
    for n in explored.nodes.values():
        g.add_node(n)
    for n in exp.new_nodes:
        g.add_node(n)
    for e in list(explored.edges):
        g.edges.append(e)
    for e in exp.new_edges:
        g.add_edge(e)  # validates endpoints loudly
    return g


def check_invariants(
    explored: RegionGraph,
    exp: Expansion,
    config: JaquaysConfig,
) -> GenerationReport:
    config.validate()
    new_ids = exp.new_region_ids()
    explored_ids = set(explored.nodes)
    is_seed = explored_ids == {explored.entrance_id}

    # All cross-boundary edges (explored ↔ new); used for topology checks.
    all_cross = [
        e
        for e in exp.new_edges
        if len(e.endpoints() & new_ids) == 1 and len(e.endpoints() & explored_ids) == 1
    ]
    # "Normal" stitches: non-hidden, non-shortcut cross-boundary edges.
    # These are the paths a player would discover by ordinary exploration.
    # Hidden and shortcut edges are secondary connections; they form loops
    # and bypasses but are not counted as independent entries.
    stitch = [e for e in all_cross if not e.hidden and not e.shortcut]
    stitch_new_endpoints = {next(iter(e.endpoints() & new_ids)) for e in stitch}
    stitch_explored_endpoints = {next(iter(e.endpoints() & explored_ids)) for e in stitch}

    post = _post_attach_graph(explored, exp)

    rep = GenerationReport(expansion_id=exp.expansion_id)
    rep.new_regions = len(exp.new_nodes)
    rep.stitch_edges = len(stitch)
    rep.loops_into_explored = max(0, len(stitch) - 1)
    rep.hidden_edges = sum(1 for e in exp.new_edges if e.hidden)

    # 1. two independent entries / no single chokepoint
    enough_stitch = len(stitch) >= config.min_stitch_edges
    enough_new = len(stitch_new_endpoints) >= config.min_stitch_edges
    if is_seed:
        entrance_links = {
            next(iter(e.endpoints() & new_ids))
            for e in stitch
            if explored.entrance_id in e.endpoints()
        }
        explored_ok = len(entrance_links) >= 2
    else:
        explored_ok = len(stitch_explored_endpoints) >= 2
    rep.invariants_passed["two_independent_entries"] = enough_stitch and enough_new and explored_ok

    # 2. loop tying back into explored (exact, see module docstring)
    rep.invariants_passed["loops_into_explored"] = (
        rep.loops_into_explored >= config.min_loops_into_explored
    )

    # 3. mix of connection types + >= 1 non-obvious edge
    kinds = {e.kind for e in exp.new_edges}
    unknown = kinds - set(config.edge_kinds)
    rep.invariants_passed["mixed_kinds_with_hidden"] = (
        not unknown and len(kinds) >= 2 and rep.hidden_edges >= config.min_hidden_edges
    )

    # 4. >= 1 shortcut that collapses distance toward the entrance
    base = post.bfs_dist(post.entrance_id)
    big = len(post.nodes) + 1
    shortcut_hits = 0
    for i, e in enumerate(post.edges):
        if not e.shortcut:
            continue
        alt = post.bfs_dist(post.entrance_id, skip_edges={i})
        gain = max((alt.get(r, big) - base.get(r, big)) for r in post.nodes)
        if gain >= config.min_shortcut_gain:
            shortcut_hits += 1
    rep.shortcut_edges = shortcut_hits
    rep.invariants_passed["shortcut_collapses_distance"] = (
        shortcut_hits >= config.min_shortcut_edges
    )

    # 5. no region with only one entrance (new regions)
    rep.invariants_passed["no_single_entrance"] = all(post.degree(rid) >= 2 for rid in new_ids)

    # no single chokepoint into new territory
    chokepoint_free = True
    for v in stitch_new_endpoints | stitch_explored_endpoints:
        if v == post.entrance_id:
            continue
        reached = set(post.bfs_dist(post.entrance_id, blocked_node=v))
        if not (new_ids - {v}) & reached:
            chokepoint_free = False
            break
    rep.invariants_passed["no_single_chokepoint"] = chokepoint_free

    produced = set(rep.invariants_passed)
    if produced != set(_INVARIANTS):
        raise RuntimeError(
            f"invariant key-set mismatch: expected {sorted(_INVARIANTS)}, "
            f"produced {sorted(produced)}"
        )
    return rep
