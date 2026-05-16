"""maze-maker family port — shared interior generators.

Every generator returns list[list[int]] indexed [y][x], FLOOR=0/WALL=1,
deterministic for a given (width, height, seed, **params).
"""

from sidequest.dungeon.interiors.generator import ALGORITHMS, generate_interior
from sidequest.dungeon.interiors.grid import FLOOR, WALL

__all__ = ["ALGORITHMS", "generate_interior", "FLOOR", "WALL"]
