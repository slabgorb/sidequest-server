"""Stage-1 expansion generation: collision-resistant sub-seeding,
candidate topology builder, re-roll loop (later task), attach (later task).

Sub-seeding uses blake2b, NOT XOR. A sibling module's braid sub-seed
`seed ^ 0x5EED` has a fixed point at seed 24301; we refuse to reproduce
that class of bug in the region-graph layer.
"""

from __future__ import annotations

import hashlib
import random

from sidequest.dungeon.region_graph.config import JaquaysConfig
from sidequest.dungeon.region_graph.model import (
    Expansion,
    RegionEdge,
    RegionGraph,
    RegionNode,
)


def _subseed(campaign_seed: int, expansion_id: int, attempt: int) -> int:
    digest = hashlib.blake2b(
        f"{campaign_seed}|{expansion_id}|{attempt}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big")


def _pick_distinct(rng: random.Random, pool: list[str], k: int) -> list[str]:
    if k >= len(pool):
        out = list(pool)
        rng.shuffle(out)
        return out
    return rng.sample(pool, k)


def _build_candidate(
    explored: RegionGraph,
    *,
    expansion_id: int,
    attach_region_ids: list[str],
    theme_pool: list[str],
    config: JaquaysConfig,
    rng: random.Random,
) -> Expansion:
    config.validate()
    if not theme_pool:
        raise ValueError("theme_pool must be non-empty")

    is_seed = set(explored.nodes) == {explored.entrance_id}
    for rid in attach_region_ids:
        if rid not in explored.nodes:
            raise ValueError(f"attach region {rid!r} is not explored")
    if is_seed:
        attach = [explored.entrance_id]
    else:
        attach = sorted(set(attach_region_ids))
        if len(attach) < 2:
            raise ValueError(
                f"expansion {expansion_id} needs >= 2 distinct attach "
                f"regions (no single chokepoint); got {attach_region_ids}"
            )

    lo, hi = config.new_regions_per_expansion
    n = rng.randint(lo, hi)
    nodes = [
        RegionNode(
            id=f"exp{expansion_id:03d}.r{i}",
            expansion_id=expansion_id,
            theme=rng.choice(theme_pool),
        )
        for i in range(n)
    ]
    new_ids = [x.id for x in nodes]
    edges: list[RegionEdge] = []

    # 1. internal spanning tree over the new regions (random parent),
    #    guarantees the expansion is internally connected.
    for i in range(1, n):
        parent = new_ids[rng.randrange(i)]
        edges.append(RegionEdge(a=parent, b=new_ids[i], kind="corridor"))

    # 2. stitch edges: floor + burst jitter, well above the minimum.
    stitch_count = config.min_stitch_edges + rng.randint(0, config.connection_burst)
    stitch_count = max(stitch_count, config.min_stitch_edges)
    new_targets = _pick_distinct(rng, list(new_ids), min(len(new_ids), stitch_count))
    while len(new_targets) < stitch_count:
        new_targets.append(new_ids[rng.randrange(len(new_ids))])
    if is_seed:
        explored_sources = [explored.entrance_id] * stitch_count
    else:
        base = _pick_distinct(rng, attach, min(len(attach), stitch_count))
        while len(base) < stitch_count:
            base.append(attach[rng.randrange(len(attach))])
        explored_sources = base
        if len(set(explored_sources[:stitch_count])) < 2 and len(attach) >= 2:
            explored_sources[1] = next(a for a in attach if a != explored_sources[0])
    for j in range(stitch_count):
        kind = "corridor" if j == 0 else rng.choice(config.edge_kinds)
        edges.append(RegionEdge(a=explored_sources[j], b=new_targets[j], kind=kind))

    # 3. hidden (non-obvious) edges: >= min_hidden_edges, kind 'secret'.
    for _ in range(config.min_hidden_edges):
        a = rng.choice(attach if not is_seed else [explored.entrance_id])
        b = rng.choice(new_ids)
        edges.append(RegionEdge(a=a, b=b, kind="secret", hidden=True))

    # 4. shortcut: deepest new region -> the explored region closest to
    #    the entrance, via a vertical-ish kind, marked shortcut.
    dist_from_entrance = explored.bfs_dist(explored.entrance_id)
    nearest = min(
        (explored.entrance_id, *attach),
        key=lambda r: dist_from_entrance.get(r, 0),
    )
    deep_new = new_ids[-1]
    shortcut_kind = rng.choice(
        [k for k in ("shaft", "chute", "stairs", "secret") if k in config.edge_kinds]
        or list(config.edge_kinds)
    )
    for _ in range(config.min_shortcut_edges):
        edges.append(
            RegionEdge(
                a=nearest,
                b=deep_new,
                kind=shortcut_kind,
                hidden=(shortcut_kind == "secret"),
                shortcut=True,
            )
        )

    # 5. extra internal loop edges scaled by burst (interior richness).
    for _ in range(rng.randint(0, config.connection_burst)):
        if n >= 2:
            x, y = rng.sample(new_ids, 2)
            edges.append(RegionEdge(a=x, b=y, kind="corridor"))

    # ensure >= 2 distinct kinds even on a tiny config
    non_corridor = [k for k in config.edge_kinds if k != "corridor"]
    if len({e.kind for e in edges}) < 2 and non_corridor:
        edges.append(
            RegionEdge(
                a=(attach[0] if not is_seed else explored.entrance_id),
                b=new_ids[0],
                kind=non_corridor[0],
            )
        )

    return Expansion(expansion_id=expansion_id, new_nodes=nodes, new_edges=edges)
