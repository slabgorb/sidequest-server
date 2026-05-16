from sidequest.dungeon.region_graph.config import JaquaysConfig
from sidequest.dungeon.region_graph.invariants import check_invariants
from sidequest.dungeon.region_graph.model import (
    Expansion,
    RegionEdge,
    RegionGraph,
    RegionNode,
)


def _explored() -> RegionGraph:
    g = RegionGraph(entrance_id="surface")
    g.add_node(RegionNode(id="surface", expansion_id=0, theme="threshold"))
    g.add_node(RegionNode(id="e1", expansion_id=1, theme="crypt"))
    g.add_node(RegionNode(id="e2", expansion_id=1, theme="crypt"))
    g.add_edge(RegionEdge(a="surface", b="e1", kind="corridor"))
    g.add_edge(RegionEdge(a="e1", b="e2", kind="corridor"))
    g.add_edge(RegionEdge(a="surface", b="e2", kind="stairs"))  # explored loop
    return g


def _good_expansion() -> Expansion:
    nodes = [
        RegionNode(id="x.r0", expansion_id=2, theme="vault"),
        RegionNode(id="x.r1", expansion_id=2, theme="vault"),
        RegionNode(id="x.r2", expansion_id=2, theme="vault"),
    ]
    edges = [
        RegionEdge(a="x.r0", b="x.r1", kind="corridor"),
        RegionEdge(a="x.r1", b="x.r2", kind="corridor"),
        RegionEdge(a="e1", b="x.r0", kind="corridor"),
        RegionEdge(a="e2", b="x.r1", kind="stairs"),
        RegionEdge(a="e1", b="x.r2", kind="secret", hidden=True),
        RegionEdge(a="surface", b="x.r2", kind="shaft", shortcut=True),
    ]
    return Expansion(expansion_id=2, new_nodes=nodes, new_edges=edges)


def test_good_expansion_passes_all_invariants():
    # minimal fixture's shortcut collapses distance by 1; spec default
    # min_shortcut_gain=3 targets real deep expansions, not unit fixtures
    rep = check_invariants(_explored(), _good_expansion(), JaquaysConfig(min_shortcut_gain=1))
    assert rep.all_passed(), rep.invariants_passed
    assert rep.stitch_edges >= 2
    assert rep.loops_into_explored >= 1
    assert rep.hidden_edges >= 1
    assert rep.shortcut_edges >= 1
    assert rep.new_regions == 3


def test_single_stitch_fails_two_independent_entries_and_loop():
    exp = _good_expansion()
    exp.new_edges = [
        RegionEdge(a="x.r0", b="x.r1", kind="corridor"),
        RegionEdge(a="x.r1", b="x.r2", kind="corridor"),
        RegionEdge(a="e1", b="x.r0", kind="corridor"),  # ONLY one stitch
        RegionEdge(a="e1", b="x.r2", kind="secret", hidden=True),
        RegionEdge(a="surface", b="x.r2", kind="shaft", shortcut=True),
    ]
    rep = check_invariants(_explored(), exp, JaquaysConfig())
    assert rep.invariants_passed["two_independent_entries"] is False
    assert rep.invariants_passed["loops_into_explored"] is False
    assert not rep.all_passed()


def test_no_hidden_edge_fails_mixed_kinds_with_hidden():
    exp = _good_expansion()
    exp.new_edges = [e for e in exp.new_edges if not e.hidden]
    exp.new_edges.append(RegionEdge(a="e1", b="x.r2", kind="corridor"))
    rep = check_invariants(_explored(), exp, JaquaysConfig())
    # only mixed_kinds_with_hidden is asserted here; other invariants'
    # states are not relevant to this test
    assert rep.invariants_passed["mixed_kinds_with_hidden"] is False


def test_missing_shortcut_fails_shortcut_invariant():
    exp = _good_expansion()
    exp.new_edges = [
        e if not e.shortcut else RegionEdge(a=e.a, b=e.b, kind="shaft") for e in exp.new_edges
    ]
    rep = check_invariants(_explored(), exp, JaquaysConfig())
    assert rep.invariants_passed["shortcut_collapses_distance"] is False


def test_degree_one_new_region_fails_no_single_entrance():
    nodes = [
        RegionNode(id="x.r0", expansion_id=2, theme="vault"),
        RegionNode(id="x.r1", expansion_id=2, theme="vault"),
        RegionNode(id="x.lonely", expansion_id=2, theme="vault"),
    ]
    edges = [
        RegionEdge(a="e1", b="x.r0", kind="corridor"),
        RegionEdge(a="e2", b="x.r1", kind="stairs"),
        RegionEdge(a="x.r0", b="x.r1", kind="corridor"),
        RegionEdge(a="x.r0", b="x.lonely", kind="corridor"),  # degree 1
        RegionEdge(a="e1", b="x.r1", kind="secret", hidden=True),
        RegionEdge(a="surface", b="x.r1", kind="shaft", shortcut=True),
    ]
    exp = Expansion(expansion_id=2, new_nodes=nodes, new_edges=edges)
    rep = check_invariants(_explored(), exp, JaquaysConfig())
    assert rep.invariants_passed["no_single_entrance"] is False


def test_seed_expansion_waives_distinct_explored_rule():
    g = RegionGraph(entrance_id="surface")
    g.add_node(RegionNode(id="surface", expansion_id=0, theme="threshold"))
    nodes = [
        RegionNode(id="s.r0", expansion_id=1, theme="crypt"),
        RegionNode(id="s.r1", expansion_id=1, theme="crypt"),
        RegionNode(id="s.r2", expansion_id=1, theme="crypt"),
    ]
    edges = [
        RegionEdge(a="surface", b="s.r0", kind="corridor"),
        RegionEdge(a="surface", b="s.r1", kind="stairs"),
        RegionEdge(a="s.r0", b="s.r1", kind="corridor"),
        RegionEdge(a="s.r1", b="s.r2", kind="corridor"),
        RegionEdge(a="s.r0", b="s.r2", kind="secret", hidden=True),
        RegionEdge(a="surface", b="s.r2", kind="shaft", shortcut=True),
    ]
    exp = Expansion(expansion_id=1, new_nodes=nodes, new_edges=edges)
    rep = check_invariants(g, exp, JaquaysConfig(min_shortcut_gain=1))
    assert rep.all_passed(), rep.invariants_passed


def test_report_is_serialisable_dict_for_otel_handoff():
    # dict-shape only, not pass-state — see test_good_expansion_passes_all_invariants for pass coverage
    rep = check_invariants(_explored(), _good_expansion(), JaquaysConfig())
    d = rep.as_dict()
    assert d["expansion_id"] == 2
    assert set(d["invariants_passed"]) == {
        "two_independent_entries",
        "loops_into_explored",
        "mixed_kinds_with_hidden",
        "shortcut_collapses_distance",
        "no_single_entrance",
        "no_single_chokepoint",
    }
    assert isinstance(d["stitch_edges"], int)
