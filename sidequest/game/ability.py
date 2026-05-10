"""Ability source classification.

ADR-007: ability is a narrative + mechanical pair. `source` records how
the ability was acquired so later systems (advancement, narrator) can
reason about its origin.

AbilitySource is defined in sidequest.protocol.models to avoid the
sidequest.game package __init__ circular dependency. It is re-exported
here so existing ``from sidequest.game.ability import AbilitySource``
call sites continue to work without change.

AbilityDefinition also lives in sidequest.protocol.models for the same
reason. Import it from there if needed outside of game/character.py.
"""

from __future__ import annotations

from sidequest.protocol.models import AbilitySource

__all__ = ["AbilitySource"]
