from sidequest.dungeon.interiors.grid import (
    FLOOR,
    WALL,
    new_grid,
    in_bounds,
    wall_neighbors,
    carve_between,
)


def test_constants():
    assert FLOOR == 0
    assert WALL == 1


def test_new_grid_is_all_wall_with_correct_shape():
    g = new_grid(width=11, height=7)
    assert len(g) == 7
    assert all(len(row) == 11 for row in g)
    assert all(cell == WALL for row in g for cell in row)


def test_in_bounds():
    g = new_grid(width=5, height=5)
    assert in_bounds(g, 0, 0)
    assert in_bounds(g, 4, 4)
    assert not in_bounds(g, 5, 4)
    assert not in_bounds(g, -1, 0)


def test_wall_neighbors_returns_two_step_wall_cells_in_bounds():
    g = new_grid(width=7, height=7)
    n = sorted(wall_neighbors(g, 3, 3))
    assert n == sorted([(1, 3), (5, 3), (3, 1), (3, 5)])


def test_wall_neighbors_excludes_floor_cells():
    g = new_grid(width=7, height=7)
    g[3][1] = FLOOR
    n = wall_neighbors(g, 3, 3)
    assert (1, 3) not in n


def test_carve_between_carves_endpoint_and_midpoint():
    g = new_grid(width=7, height=7)
    carve_between(g, 3, 3, 5, 3)
    assert g[3][3] == FLOOR
    assert g[3][5] == FLOOR
    assert g[3][4] == FLOOR
