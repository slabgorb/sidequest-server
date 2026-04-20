"""Ability source classification.

Port of sidequest_game::ability (ability.rs, ~55 LOC).
ADR-007: ability is a narrative + mechanical pair. `source` records how
the ability was acquired so later systems (advancement, narrator) can
reason about its origin.

AbilityDefinition itself lives in sidequest/game/character.py alongside
Character for now, because the Phase 1 port co-located them there. The
Rust tree keeps the pair together in ability.rs; a future cleanup can
pull AbilityDefinition over to match.
"""

from __future__ import annotations

from enum import Enum


class AbilitySource(str, Enum):
    """How a character acquired an ability.

    Port of sidequest_game::ability::AbilitySource.
    """

    Race = "Race"
    """Innate to the character's race/species."""
    Class = "Class"
    """Granted by the character's class/archetype."""
    Item = "Item"
    """Bestowed by an item or artifact."""
    Play = "Play"
    """Acquired during gameplay through experience."""


__all__ = ["AbilitySource"]
