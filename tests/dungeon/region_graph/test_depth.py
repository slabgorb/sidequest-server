"""Unit tests for sidequest.dungeon.region_graph.depth."""

import dataclasses

import pytest

from sidequest.dungeon.region_graph.depth import DepthConfig, ordinary_route_dist
from sidequest.dungeon.region_graph.model import RegionEdge, RegionGraph, RegionNode


def test_depth_config_defaults():
    c = DepthConfig()
    assert c.depth_per_hop == 10.0
    assert c.jitter_max == 3.0
    assert c.bucket_size == 30.0  # §12 decision: 3 ordinary hops per "level"
    c.validate()  # defaults are self-consistent


@pytest.mark.parametrize(
    "kwargs, msg",
    [
        ({"depth_per_hop": 0.0}, "depth_per_hop must be > 0"),
        ({"depth_per_hop": -1.0}, "depth_per_hop must be > 0"),
        ({"jitter_max": -0.1}, "jitter_max must be >= 0"),
        ({"bucket_size": 9.9}, "bucket_size must be >= depth_per_hop"),
    ],
    ids=["hop-zero", "hop-negative", "jitter-negative", "bucket-below-hop"],
)
def test_depth_config_validate_rejects(kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        DepthConfig(**kwargs).validate()


def test_depth_config_bucket_equal_to_hop_is_valid():
    DepthConfig(depth_per_hop=10.0, bucket_size=10.0).validate()  # equality is allowed


def test_depth_config_zero_jitter_is_valid():
    DepthConfig(jitter_max=0.0).validate()  # jitter is optional (spec §5)


def test_region_node_depth_score_defaults_none():
    n = RegionNode(id="r0", expansion_id=0, theme="crypt")
    assert n.depth_score is None  # unassigned until attach (real default, not a stub)


def test_region_node_depth_score_set_via_replace():
    n = RegionNode(id="r0", expansion_id=0, theme="crypt")
    scored = dataclasses.replace(n, depth_score=42.0)
    assert scored.depth_score == 42.0
    assert scored.id == "r0" and scored.theme == "crypt"
    assert n.depth_score is None  # original frozen instance untouched


def _chain_graph() -> RegionGraph:
    # entrance -corridor- a -corridor- b ; plus a SHORTCUT entrance->b
    g = RegionGraph(entrance_id="e")
    for rid in ("e", "a", "b"):
        g.add_node(RegionNode(id=rid, expansion_id=0, theme="t"))
    g.add_edge(RegionEdge(a="e", b="a", kind="corridor"))
    g.add_edge(RegionEdge(a="a", b="b", kind="corridor"))
    g.add_edge(RegionEdge(a="e", b="b", kind="shaft", shortcut=True))
    return g


def test_ordinary_route_ignores_shortcut():
    g = _chain_graph()
    d = ordinary_route_dist(g)
    # shortcut e->b is excluded: b is 2 hops via a, not 1 via the shortcut
    assert d == {"e": 0, "a": 1, "b": 2}


def test_ordinary_route_ignores_hidden():
    g = RegionGraph(entrance_id="e")
    for rid in ("e", "a", "b"):
        g.add_node(RegionNode(id=rid, expansion_id=0, theme="t"))
    g.add_edge(RegionEdge(a="e", b="a", kind="corridor"))
    g.add_edge(RegionEdge(a="a", b="b", kind="corridor"))
    g.add_edge(RegionEdge(a="e", b="b", kind="secret", hidden=True))
    assert ordinary_route_dist(g) == {"e": 0, "a": 1, "b": 2}


def test_ordinary_route_raises_when_region_unreachable_on_ordinary_graph():
    # b reachable ONLY via a hidden edge -> No Silent Fallbacks: fail loud
    g = RegionGraph(entrance_id="e")
    for rid in ("e", "b"):
        g.add_node(RegionNode(id=rid, expansion_id=0, theme="t"))
    g.add_edge(RegionEdge(a="e", b="b", kind="secret", hidden=True))
    with pytest.raises(ValueError, match="not reachable on the ordinary route"):
        ordinary_route_dist(g)


def test_ordinary_route_single_node():
    g = RegionGraph(entrance_id="e")
    g.add_node(RegionNode(id="e", expansion_id=0, theme="t"))
    assert ordinary_route_dist(g) == {"e": 0}  # seed graph: entrance only, no raise
