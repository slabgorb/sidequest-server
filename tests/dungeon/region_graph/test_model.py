import pytest

from sidequest.dungeon.region_graph.model import (
    Expansion,
    RegionEdge,
    RegionGraph,
    RegionNode,
)


def _entrance_graph() -> RegionGraph:
    g = RegionGraph(entrance_id="surface")
    g.add_node(RegionNode(id="surface", expansion_id=0, theme="town_threshold"))
    return g


def test_add_node_and_edge_basic():
    g = _entrance_graph()
    g.add_node(RegionNode(id="a", expansion_id=1, theme="crypt"))
    g.add_edge(RegionEdge(a="surface", b="a", kind="corridor"))
    assert g.neighbors("surface") == ["a"]
    assert g.degree("a") == 1


def test_duplicate_node_id_raises_loudly():
    g = _entrance_graph()
    with pytest.raises(ValueError, match="duplicate region id 'surface'"):
        g.add_node(RegionNode(id="surface", expansion_id=0, theme="x"))


def test_edge_to_unknown_endpoint_raises_loudly():
    g = _entrance_graph()
    with pytest.raises(ValueError, match="edge endpoint 'ghost' is not a known region"):
        g.add_edge(RegionEdge(a="surface", b="ghost", kind="corridor"))


def test_self_loop_edge_raises_loudly():
    g = _entrance_graph()
    with pytest.raises(ValueError, match="self-loop edge on 'surface'"):
        g.add_edge(RegionEdge(a="surface", b="surface", kind="corridor"))


def test_bfs_dist_and_reachability():
    g = _entrance_graph()
    for rid in ("a", "b", "c"):
        g.add_node(RegionNode(id=rid, expansion_id=1, theme="crypt"))
    g.add_edge(RegionEdge(a="surface", b="a", kind="corridor"))
    g.add_edge(RegionEdge(a="a", b="b", kind="corridor"))
    g.add_edge(RegionEdge(a="b", b="c", kind="corridor"))
    dist = g.bfs_dist("surface")
    assert dist == {"surface": 0, "a": 1, "b": 2, "c": 3}
    assert g.reachable_from_entrance() == {"surface", "a", "b", "c"}
    assert g.is_connected() is True


def test_bfs_dist_skip_edges_reroutes():
    g = _entrance_graph()
    for rid in ("a", "b"):
        g.add_node(RegionNode(id=rid, expansion_id=1, theme="crypt"))
    g.add_edge(RegionEdge(a="surface", b="a", kind="corridor"))  # idx 0
    g.add_edge(RegionEdge(a="a", b="b", kind="corridor"))  # idx 1
    g.add_edge(RegionEdge(a="surface", b="b", kind="shaft"))  # idx 2 (shortcut)
    assert g.bfs_dist("surface")["b"] == 1
    assert g.bfs_dist("surface", skip_edges={2})["b"] == 2


def test_cyclomatic_number_counts_independent_loops():
    g = _entrance_graph()
    for rid in ("a", "b"):
        g.add_node(RegionNode(id=rid, expansion_id=1, theme="crypt"))
    g.add_edge(RegionEdge(a="surface", b="a", kind="corridor"))
    g.add_edge(RegionEdge(a="a", b="b", kind="corridor"))
    assert g.cyclomatic_number() == 0  # tree
    g.add_edge(RegionEdge(a="b", b="surface", kind="secret"))
    assert g.cyclomatic_number() == 1  # one loop
    assert g.is_connected() is True


def test_cyclomatic_counts_components():
    g = _entrance_graph()
    g.add_node(RegionNode(id="island", expansion_id=9, theme="crypt"))  # disconnected
    assert g._component_count() == 2
    assert g.cyclomatic_number() == 0
    assert g.is_connected() is False


def test_expansion_is_a_plain_node_edge_bundle():
    exp = Expansion(
        expansion_id=3,
        new_nodes=[RegionNode(id="exp003.r0", expansion_id=3, theme="vault")],
        new_edges=[RegionEdge(a="surface", b="exp003.r0", kind="stairs", hidden=True)],
    )
    assert exp.new_region_ids() == {"exp003.r0"}


def test_bfs_dist_unknown_source_raises_loudly():
    g = _entrance_graph()
    with pytest.raises(ValueError, match="bfs_dist source 'ghost' is not a known region"):
        g.bfs_dist("ghost")


def test_neighbors_unknown_region_raises_loudly():
    g = _entrance_graph()
    with pytest.raises(ValueError, match="region 'ghost' is not in this graph"):
        g.neighbors("ghost")


def test_degree_unknown_region_raises_loudly():
    g = _entrance_graph()
    with pytest.raises(ValueError, match="region 'ghost' is not in this graph"):
        g.degree("ghost")
