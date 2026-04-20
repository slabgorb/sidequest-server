"""sidequest.genre.archetype — archetype resolution.

Port of sidequest-genre/src/archetype/ (mod.rs, resolved.rs, shim.rs).
"""

from sidequest.genre.archetype.resolved import ArchetypeResolved
from sidequest.genre.archetype.shim import (
    ArchetypeResolution,
    ResolutionSource,
    resolve_archetype,
)

__all__ = [
    "ArchetypeResolved",
    "ArchetypeResolution",
    "ResolutionSource",
    "resolve_archetype",
]
