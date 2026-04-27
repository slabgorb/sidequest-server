"""Ability source classification.

ADR-007: ability is a narrative + mechanical pair. `source` records how
the ability was acquired so later systems (advancement, narrator) can
reason about its origin.

AbilityDefinition itself lives in sidequest/game/character.py alongside
Character for now; a future cleanup can pull AbilityDefinition over to
match.
"""

from __future__ import annotations

from enum import Enum


class AbilitySource(str, Enum):
    """How a character acquired an ability."""

    Race = "Race"
    """Innate to the character's race/species."""
    Class = "Class"
    """Granted by the character's class/archetype."""
    Item = "Item"
    """Bestowed by an item or artifact."""
    Play = "Play"
    """Acquired during gameplay through experience."""


__all__ = ["AbilitySource"]
