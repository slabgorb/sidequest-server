import pytest

from sidequest.dungeon.region_graph import (
    JaquaysConfig,
    RegionGraph,
    RegionNode,
    attach_expansion,
    check_invariants,
    generate_expansion,
)

THEMES = ["crypt", "vault", "flooded", "catacomb", "undercity"]


def _seed_graph() -> RegionGraph:
    g = RegionGraph(entrance_id="surface")
    g.add_node(RegionNode(id="surface", expansion_id=0, theme="threshold"))
    return g


def _grow(campaign_seed: int, depth: int, cfg: JaquaysConfig) -> RegionGraph:
    g = _seed_graph()
    frontier = ["surface"]
    for eid in range(depth):
        attach_ids = ["surface"] if set(g.nodes) == {"surface"} else sorted(frontier)[-3:]
        exp, rep = generate_expansion(
            graph=g,
            campaign_seed=campaign_seed,
            expansion_id=eid,
            attach_region_ids=attach_ids,
            theme_pool=THEMES,
            config=cfg,
        )
        assert rep.all_passed(), (eid, rep.failing())
        attach_expansion(g, exp)
        frontier = sorted(exp.new_region_ids())
    return g


@pytest.mark.parametrize("campaign_seed", range(40))
def test_deep_chain_holds_every_invariant(campaign_seed):
    cfg = JaquaysConfig()
    g = _seed_graph()
    frontier = ["surface"]
    for eid in range(15):
        attach_ids = ["surface"] if set(g.nodes) == {"surface"} else sorted(frontier)[-3:]
        pre_cyc = g.cyclomatic_number()
        exp, rep = generate_expansion(
            graph=g,
            campaign_seed=campaign_seed,
            expansion_id=eid,
            attach_region_ids=attach_ids,
            theme_pool=THEMES,
            config=cfg,
        )
        assert check_invariants(g, exp, cfg).all_passed()
        attach_expansion(g, exp)
        assert g.is_connected()  # solvability
        assert g.cyclomatic_number() >= max(1, pre_cyc)  # loopful, monotone
        frontier = sorted(exp.new_region_ids())
    assert all("floor" not in nid for nid in g.nodes)
    assert len(g.nodes) > 15


def test_chain_is_deterministic_pre_curation():
    cfg = JaquaysConfig()
    g1 = _grow(2026, 10, cfg)
    g2 = _grow(2026, 10, cfg)
    assert sorted(g1.nodes) == sorted(g2.nodes)
    assert [(e.a, e.b, e.kind, e.hidden, e.shortcut) for e in g1.edges] == [
        (e.a, e.b, e.kind, e.hidden, e.shortcut) for e in g2.edges
    ]


def test_burst_increases_connection_richness_across_sweep():
    thin = sum(len(_grow(s, 8, JaquaysConfig(connection_burst=0)).edges) for s in range(15))
    fat = sum(len(_grow(s, 8, JaquaysConfig(connection_burst=8)).edges) for s in range(15))
    assert fat > thin
