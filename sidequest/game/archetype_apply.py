"""Apply an archetype resolution to a Character.

Port of sidequest_game::character::Character::apply_archetype_resolved
(character.rs:96-99).

The dispatch layer calls this after the archetype shim
(sidequest.genre.archetype.shim.resolve_archetype) produces an
ArchetypeResolution, keeping ``resolved_archetype`` (display name) and
``archetype_provenance`` (tier + merge trail) in lockstep on the Character.
"""

from __future__ import annotations

from sidequest.game.character import Character
from sidequest.genre.archetype.shim import ArchetypeResolution


def apply_archetype_resolved(
    character: Character, resolution: ArchetypeResolution
) -> None:
    """Stamp the resolved archetype's display name and provenance onto a Character.

    Mirrors Rust ``Character::apply_archetype_resolved(&Resolved<ArchetypeResolved>)``.
    The two fields MUST be set together — they describe the same resolution
    and the GM panel reads them as a pair.
    """
    character.resolved_archetype = resolution.resolved.name
    character.archetype_provenance = resolution.provenance.model_dump(mode="json")
