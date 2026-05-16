"""Braid post-process: remove a fraction of dead-ends to add loops.

Spec §5.2 mitigation for perfect-maze interiors. braid_ratio in
[0.0, 1.0]: 0.0 leaves the maze untouched; 1.0 removes ALL ORIGINAL
dead-ends for depth-first mazes (where 2-step carving guarantees the
opened wall gains two floor neighbors). For non-2-step generators
(e.g. prim) a single pass over the original snapshot may leave a few
residual dead-ends — that is expected, not a bug. Deterministic given
(grid, seed, braid_ratio). Only carves FLOOR; never walls an existing
FLOOR cell.
"""

from __future__ import annotations

import random

from sidequest.dungeon.interiors.grid import FLOOR, WALL, Grid, in_bounds

_ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))


def dead_ends(grid: Grid) -> list[tuple[int, int]]:
    """FLOOR cells with exactly one FLOOR orthogonal neighbor."""
    out: list[tuple[int, int]] = []
    for y in range(len(grid)):
        for x in range(len(grid[0])):
            if grid[y][x] != FLOOR:
                continue
            n = sum(
                1
                for dx, dy in _ORTHO
                if in_bounds(grid, x + dx, y + dy) and grid[y + dy][x + dx] == FLOOR
            )
            if n == 1:
                out.append((x, y))
    return out


def braid(grid: Grid, *, seed: int, braid_ratio: float) -> Grid:
    """Carve walls adjacent to a braid_ratio fraction of dead-ends."""
    if braid_ratio <= 0.0:
        return grid
    rng = random.Random(seed)
    original = dead_ends(grid)
    target_removed = int(round(len(original) * min(1.0, braid_ratio)))
    if target_removed <= 0:
        return grid

    removed = 0
    pending = sorted(original)
    rng.shuffle(pending)
    for x, y in pending:
        if removed >= target_removed:
            break
        floor_n = [
            (x + dx, y + dy)
            for dx, dy in _ORTHO
            if in_bounds(grid, x + dx, y + dy) and grid[y + dy][x + dx] == FLOOR
        ]
        if len(floor_n) != 1:
            continue
        wall_n = [
            (x + dx, y + dy)
            for dx, dy in _ORTHO
            if in_bounds(grid, x + dx, y + dy)
            and grid[y + dy][x + dx] == WALL
            and 0 < x + dx < len(grid[0]) - 1
            and 0 < y + dy < len(grid) - 1
        ]
        if not wall_n:
            continue
        nx, ny = wall_n[rng.randrange(len(wall_n))]
        grid[ny][nx] = FLOOR
        removed += 1
    return grid
