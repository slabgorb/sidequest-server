import pytest

from sidequest.dungeon.interiors.cellular import gen_cave
from sidequest.dungeon.interiors.grid import FLOOR, WALL


def test_shape():
    g = gen_cave(width=18, height=18, seed=1042)
    assert len(g) == 18 and all(len(r) == 18 for r in g)


def test_borders_are_walls():
    g = gen_cave(width=18, height=18, seed=1042)
    for x in range(18):
        assert g[0][x] == WALL and g[17][x] == WALL
    for y in range(18):
        assert g[y][0] == WALL and g[y][17] == WALL


def test_deterministic():
    assert gen_cave(width=18, height=18, seed=7) == gen_cave(width=18, height=18, seed=7)


def test_seed_variance():
    assert gen_cave(width=18, height=18, seed=7) != gen_cave(width=18, height=18, seed=8)


def test_value_domain():
    g = gen_cave(width=18, height=18, seed=7)
    assert all(c in (FLOOR, WALL) for row in g for c in row)


def test_single_connected_floor_component():
    g = gen_cave(width=24, height=24, seed=99)
    h, w = len(g), len(g[0])
    start = next((x, y) for y in range(h) for x in range(w) if g[y][x] == FLOOR)
    seen = {start}
    stack = [start]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and g[ny][nx] == FLOOR and (nx, ny) not in seen:
                seen.add((nx, ny))
                stack.append((nx, ny))
    assert len(seen) == sum(1 for r in g for c in r if c == FLOOR)


def test_rejects_too_small():
    with pytest.raises(ValueError, match="width>=3 and height>=3"):
        gen_cave(width=2, height=2, seed=0)
