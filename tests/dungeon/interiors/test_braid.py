from sidequest.dungeon.interiors.braid import braid, dead_ends
from sidequest.dungeon.interiors.depthfirst import gen_depthfirst
from sidequest.dungeon.interiors.grid import FLOOR


def test_ratio_zero_is_identity():
    g = gen_depthfirst(width=31, height=31, seed=11)
    out = braid([r[:] for r in g], seed=1, braid_ratio=0.0)
    assert out == g


def test_ratio_one_removes_all_dead_ends():
    g = gen_depthfirst(width=31, height=31, seed=11)
    out = braid([r[:] for r in g], seed=1, braid_ratio=1.0)
    assert dead_ends(out) == []


def test_partial_ratio_reduces_dead_ends_monotonically():
    g = gen_depthfirst(width=41, height=41, seed=4)
    base = len(dead_ends(g))
    half = len(dead_ends(braid([r[:] for r in g], seed=1, braid_ratio=0.5)))
    full = len(dead_ends(braid([r[:] for r in g], seed=1, braid_ratio=1.0)))
    assert base >= half >= full
    assert half < base


def test_deterministic():
    g = gen_depthfirst(width=31, height=31, seed=11)
    a = braid([r[:] for r in g], seed=2, braid_ratio=0.3)
    b = braid([r[:] for r in g], seed=2, braid_ratio=0.3)
    assert a == b


def test_braid_only_adds_floor_never_removes():
    g = gen_depthfirst(width=31, height=31, seed=11)
    out = braid([r[:] for r in g], seed=2, braid_ratio=0.7)
    for y in range(len(g)):
        for x in range(len(g[0])):
            if g[y][x] == FLOOR:
                assert out[y][x] == FLOOR
