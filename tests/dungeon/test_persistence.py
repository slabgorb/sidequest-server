"""Beneath Sünden Plan 5 — persistence layer tests.

Round-trip, freeze, no-floor, overlay, ledger, OTEL, and the Plan-7
wiring contract. Real SQLite only (:memory: + temp-file for WAL).
"""

from __future__ import annotations


def test_persistence_module_importable() -> None:
    import sidequest.dungeon.persistence as persistence

    assert hasattr(persistence, "DungeonStore")


from sidequest.dungeon.region_graph.model import RegionEdge, RegionNode  # noqa: E402


def test_region_node_dict_roundtrip_exact_inverse() -> None:
    n = RegionNode(id="exp001.r0", expansion_id=1, theme="crypt", depth_score=42.5)
    assert RegionNode.from_dict(n.to_dict()) == n

    n_unscored = RegionNode(id="entrance", expansion_id=0, theme="threshold")
    d = n_unscored.to_dict()
    assert d["depth_score"] is None
    assert RegionNode.from_dict(d) == n_unscored


def test_region_edge_dict_roundtrip_exact_inverse() -> None:
    e = RegionEdge(a="entrance", b="exp001.r0", kind="secret", hidden=True, shortcut=True)
    assert RegionEdge.from_dict(e.to_dict()) == e

    plain = RegionEdge(a="x", b="y", kind="corridor")
    d = plain.to_dict()
    assert d["hidden"] is False and d["shortcut"] is False
    assert RegionEdge.from_dict(d) == plain


from sidequest.dungeon.region_graph.model import RegionGraph  # noqa: E402


def test_region_graph_dict_roundtrip_exact_inverse() -> None:
    g = RegionGraph(entrance_id="entrance")
    g.add_node(RegionNode(id="entrance", expansion_id=0, theme="threshold", depth_score=0.0))
    g.add_node(RegionNode(id="exp001.r0", expansion_id=1, theme="crypt", depth_score=10.0))
    g.add_node(RegionNode(id="exp001.r1", expansion_id=1, theme="crypt", depth_score=12.0))
    g.add_edge(RegionEdge(a="entrance", b="exp001.r0", kind="corridor"))
    g.add_edge(RegionEdge(a="exp001.r0", b="exp001.r1", kind="stairs"))
    g.add_edge(RegionEdge(a="entrance", b="exp001.r1", kind="secret", hidden=True))

    restored = RegionGraph.from_dict(g.to_dict())
    assert restored.entrance_id == g.entrance_id
    assert restored.nodes == g.nodes
    assert restored.edges == g.edges
