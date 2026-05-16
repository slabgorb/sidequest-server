"""Region-graph data model.

A region is a themed zone (node). An edge is a typed connection
(corridor|stairs|shaft|chute|secret) optionally hidden (secret/conditional)
and optionally a shortcut (collapses distance toward the surface entrance).
The contiguous map is keyed by region/expansion id, never by floor
(spec decision rows 1, 2).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RegionNode:
    id: str
    expansion_id: int
    theme: str


@dataclass(frozen=True)
class RegionEdge:
    a: str
    b: str
    kind: str
    hidden: bool = False
    shortcut: bool = False

    def endpoints(self) -> frozenset[str]:
        return frozenset((self.a, self.b))


@dataclass
class Expansion:
    """A candidate batch of new regions + edges (edges may reference
    already-explored region ids for stitch/shortcut connections).

    Edges in ``new_edges`` may reference region ids from the parent graph;
    add ``new_nodes`` to that graph before calling ``add_edge`` for
    ``new_edges``.
    """

    expansion_id: int
    new_nodes: list[RegionNode]
    new_edges: list[RegionEdge]

    def new_region_ids(self) -> set[str]:
        return {n.id for n in self.new_nodes}


@dataclass
class RegionGraph:
    entrance_id: str
    nodes: dict[str, RegionNode] = field(default_factory=dict)
    edges: list[RegionEdge] = field(default_factory=list)

    def add_node(self, node: RegionNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"duplicate region id {node.id!r}")
        self.nodes[node.id] = node

    def add_edge(self, edge: RegionEdge) -> None:
        if edge.a == edge.b:
            raise ValueError(f"self-loop edge on {edge.a!r} is not allowed")
        for end in (edge.a, edge.b):
            if end not in self.nodes:
                raise ValueError(f"edge endpoint {end!r} is not a known region")
        self.edges.append(edge)

    def neighbors(self, region_id: str) -> list[str]:
        if region_id not in self.nodes:
            raise ValueError(f"region {region_id!r} is not in this graph")
        out: list[str] = []
        for e in self.edges:
            if e.a == region_id:
                out.append(e.b)
            elif e.b == region_id:
                out.append(e.a)
        return out

    def degree(self, region_id: str) -> int:
        if region_id not in self.nodes:
            raise ValueError(f"region {region_id!r} is not in this graph")
        return sum(1 for e in self.edges if region_id in (e.a, e.b))

    def bfs_dist(
        self,
        source: str,
        *,
        blocked_node: str | None = None,
        skip_edges: set[int] | None = None,
    ) -> dict[str, int]:
        if source not in self.nodes:
            raise ValueError(f"bfs_dist source {source!r} is not a known region")
        skip = skip_edges or set()
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for i, e in enumerate(self.edges):
            if i in skip:
                continue
            adj[e.a].append(e.b)
            adj[e.b].append(e.a)
        dist: dict[str, int] = {source: 0}
        q: deque[str] = deque([source])
        while q:
            cur = q.popleft()
            for nxt in adj[cur]:
                if nxt == blocked_node:
                    continue
                if nxt not in dist:
                    dist[nxt] = dist[cur] + 1
                    q.append(nxt)
        return dist

    def reachable_from_entrance(self) -> set[str]:
        return set(self.bfs_dist(self.entrance_id))

    def is_connected(self) -> bool:
        if not self.nodes:
            return True
        return len(self.reachable_from_entrance()) == len(self.nodes)

    def _component_count(self) -> int:
        seen: set[str] = set()
        comps = 0
        for n in self.nodes:
            if n in seen:
                continue
            comps += 1
            seen |= set(self.bfs_dist(n))
        return comps

    def cyclomatic_number(self) -> int:
        """|E| - |V| + components. 0 == acyclic (forest, possibly
        disconnected); >=1 means at least one cycle."""
        return len(self.edges) - len(self.nodes) + self._component_count()
