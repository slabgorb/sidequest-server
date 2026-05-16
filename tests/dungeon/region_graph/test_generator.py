import random

import pytest

from sidequest.dungeon.region_graph.config import JaquaysConfig
from sidequest.dungeon.region_graph.generator import _build_candidate, _subseed
from sidequest.dungeon.region_graph.model import RegionEdge, RegionGraph, RegionNode

THEMES = ["crypt", "vault", "flooded", "catacomb"]


def _explored() -> RegionGraph:
    g = RegionGraph(entrance_id="surface")
    g.add_node(RegionNode(id="surface", expansion_id=0, theme="threshold"))
    for i in range(4):
        g.add_node(RegionNode(id=f"e{i}", expansion_id=1, theme="crypt"))
    chain = ["surface", "e0", "e1", "e2", "e3"]
    for a, b in zip(chain, chain[1:], strict=False):
        g.add_edge(RegionEdge(a=a, b=b, kind="corridor"))
    g.add_edge(RegionEdge(a="surface", b="e3", kind="stairs"))  # explored loop
    return g


def test_subseed_is_deterministic_and_wide():
    s1 = _subseed(123, 4, 0)
    s2 = _subseed(123, 4, 0)
    assert s1 == s2
    assert s1 != _subseed(123, 4, 1)
    assert s1 != _subseed(124, 4, 0)
    assert 0 <= s1 < 2**64


def test_subseed_has_no_xor_fixed_point_regression():
    """Sibling braid bug: `seed ^ 0x5EED` collides at 24301.
    blake2b mixing must not reproduce that class of fixed point."""
    bad = 0x5EED  # 24301
    seeds = {_subseed(bad, e, 0) for e in range(50)}
    assert len(seeds) == 50  # all distinct, none zeroed
    assert all(s != 0 for s in seeds)
    assert _subseed(bad, 0, 0) != _subseed(0, 0, 0)


def test_build_candidate_is_deterministic():
    g = _explored()
    cfg = JaquaysConfig()
    a = _build_candidate(
        g,
        expansion_id=2,
        attach_region_ids=["e2", "e3"],
        theme_pool=THEMES,
        config=cfg,
        rng=random.Random(_subseed(7, 2, 0)),
    )
    b = _build_candidate(
        g,
        expansion_id=2,
        attach_region_ids=["e2", "e3"],
        theme_pool=THEMES,
        config=cfg,
        rng=random.Random(_subseed(7, 2, 0)),
    )
    assert [n.id for n in a.new_nodes] == [n.id for n in b.new_nodes]
    assert [(e.a, e.b, e.kind, e.hidden, e.shortcut) for e in a.new_edges] == [
        (e.a, e.b, e.kind, e.hidden, e.shortcut) for e in b.new_edges
    ]


def test_build_candidate_shapes_within_config_bounds():
    g = _explored()
    cfg = JaquaysConfig()
    exp = _build_candidate(
        g,
        expansion_id=2,
        attach_region_ids=["e2", "e3"],
        theme_pool=THEMES,
        config=cfg,
        rng=random.Random(_subseed(1, 2, 0)),
    )
    lo, hi = cfg.new_regions_per_expansion
    assert lo <= len(exp.new_nodes) <= hi
    assert all(n.theme in THEMES for n in exp.new_nodes)
    assert all(n.id.startswith("exp002.") for n in exp.new_nodes)
    stitch = [e for e in exp.new_edges if len({e.a, e.b} & {n.id for n in exp.new_nodes}) == 1]
    assert len(stitch) >= cfg.min_stitch_edges
    assert any(e.hidden for e in exp.new_edges)
    assert any(e.shortcut for e in exp.new_edges)


def test_higher_burst_yields_more_stitch_on_average():
    g = _explored()

    def total_stitch(burst: int) -> int:
        cfg = JaquaysConfig(connection_burst=burst)
        t = 0
        for seed in range(40):
            exp = _build_candidate(
                g,
                expansion_id=2,
                attach_region_ids=["e1", "e2", "e3"],
                theme_pool=THEMES,
                config=cfg,
                rng=random.Random(_subseed(seed, 2, 0)),
            )
            t += sum(
                1 for e in exp.new_edges if len({e.a, e.b} & {n.id for n in exp.new_nodes}) == 1
            )
        return t

    s0, s3, s8 = total_stitch(0), total_stitch(3), total_stitch(8)
    assert s0 < s3 < s8


def test_shortcut_target_is_not_a_stitch_target():
    g = _explored()
    cfg = JaquaysConfig()
    for seed in range(40):
        exp = _build_candidate(
            g,
            expansion_id=2,
            attach_region_ids=["e1", "e2", "e3"],
            theme_pool=THEMES,
            config=cfg,
            rng=random.Random(_subseed(seed, 2, 0)),
        )
        new = {n.id for n in exp.new_nodes}
        shortcut_targets = {(e.b if e.a not in new else e.a) for e in exp.new_edges if e.shortcut}
        normal_stitch_new = {
            (e.a if e.a in new else e.b)
            for e in exp.new_edges
            if not e.hidden and not e.shortcut and len({e.a, e.b} & new) == 1
        }
        # the shortcut's deep target must NOT also be a normal stitch entry
        assert shortcut_targets.isdisjoint(normal_stitch_new), (
            seed,
            shortcut_targets,
            normal_stitch_new,
        )


def test_attach_region_ids_must_be_explored_for_non_seed():
    g = _explored()
    with pytest.raises(ValueError, match="attach region 'nope' is not explored"):
        _build_candidate(
            g,
            expansion_id=2,
            attach_region_ids=["nope"],
            theme_pool=THEMES,
            config=JaquaysConfig(),
            rng=random.Random(1),
        )


def test_non_seed_requires_two_attach_regions():
    g = _explored()
    with pytest.raises(ValueError, match="needs >= 2 distinct attach regions"):
        _build_candidate(
            g,
            expansion_id=2,
            attach_region_ids=["e1"],
            theme_pool=THEMES,
            config=JaquaysConfig(),
            rng=random.Random(1),
        )


def test_empty_theme_pool_raises_loudly():
    g = _explored()
    with pytest.raises(ValueError, match="theme_pool must be non-empty"):
        _build_candidate(
            g,
            expansion_id=2,
            attach_region_ids=["e2", "e3"],
            theme_pool=[],
            config=JaquaysConfig(),
            rng=random.Random(1),
        )
