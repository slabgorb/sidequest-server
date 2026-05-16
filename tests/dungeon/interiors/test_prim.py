import pytest

from sidequest.dungeon.interiors.grid import FLOOR, WALL
from sidequest.dungeon.interiors.prim import gen_prim


def test_shape():
    g = gen_prim(width=25, height=25, seed=5)
    assert len(g) == 25 and all(len(r) == 25 for r in g)


def test_deterministic():
    assert gen_prim(width=25, height=25, seed=5) == gen_prim(width=25, height=25, seed=5)


def test_seed_variance():
    assert gen_prim(width=25, height=25, seed=5) != gen_prim(width=25, height=25, seed=6)


def test_value_domain():
    g = gen_prim(width=25, height=25, seed=5)
    assert all(c in (FLOOR, WALL) for row in g for c in row)


def test_carves_floor_proportional_to_density_and_complexity():
    sparse = gen_prim(width=41, height=41, seed=5, density=1, complexity=4)
    dense = gen_prim(width=41, height=41, seed=5, density=12, complexity=40)
    floor = lambda g: sum(1 for r in g for c in r if c == FLOOR)  # noqa: E731
    assert floor(dense) > floor(sparse)


def test_explicit_params_are_deterministic():
    a = gen_prim(width=31, height=31, seed=9, density=5, complexity=20)
    b = gen_prim(width=31, height=31, seed=9, density=5, complexity=20)
    assert a == b


def test_rejects_too_small():
    with pytest.raises(ValueError, match="width>=3 and height>=3"):
        gen_prim(width=2, height=2, seed=0)
