"""Shared grid model + carve helpers for the maze-maker family port.

Convention matches the original maze-maker `Maze` base and the
existing cellular port: grid[y][x], FLOOR=0, WALL=1, carve generators
work on odd coordinates with midpoint carving.
"""

from __future__ import annotations

FLOOR = 0
WALL = 1

Grid = list[list[int]]


def new_grid(width: int, height: int) -> Grid:
    """Return a height x width grid filled entirely with WALL."""
    return [[WALL for _ in range(width)] for _ in range(height)]


def in_bounds(grid: Grid, x: int, y: int) -> bool:
    return 0 <= y < len(grid) and 0 <= x < len(grid[0])


def wall_neighbors(grid: Grid, x: int, y: int) -> list[tuple[int, int]]:
    """Two-step neighbors (maze-maker `walls`) that are in-bounds and WALL."""
    out: list[tuple[int, int]] = []
    for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2)):
        nx, ny = x + dx, y + dy
        if in_bounds(grid, nx, ny) and grid[ny][nx] == WALL:
            out.append((nx, ny))
    return out


def carve_between(grid: Grid, x0: int, y0: int, x1: int, y1: int) -> None:
    """Carve both endpoints and the midpoint to FLOOR (maze-maker carve)."""
    grid[y0][x0] = FLOOR
    grid[y1][x1] = FLOOR
    grid[(y0 + y1) // 2][(x0 + x1) // 2] = FLOOR
