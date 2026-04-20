"""sidequest.genre — genre pack loading, resolution, and models.

Public re-exports from foundation layer (Story 41-2, subagent A).
Subagent B will add GenrePack and other model types.
Subagent C will add GenreLoader and the archetype shim.
"""

from sidequest.genre.cache import GenreCache
from sidequest.genre.error import (
    GenreCycleError,
    GenreError,
    GenreIoError,
    GenreLoadError,
    GenreMissingParentError,
    GenreNotFoundError,
    GenreValidationError,
    SchemaValidationError,
    ValidationErrors,
)
from sidequest.genre.resolver import (
    LayeredMerge,
    MergeStrategy,
    ResolutionContext,
    Resolved,
    Resolver,
)

__all__ = [
    # resolver
    "LayeredMerge",
    "MergeStrategy",
    "ResolutionContext",
    "Resolved",
    "Resolver",
    # errors
    "GenreError",
    "GenreLoadError",
    "GenreCycleError",
    "GenreMissingParentError",
    "GenreValidationError",
    "GenreIoError",
    "GenreNotFoundError",
    "SchemaValidationError",
    "ValidationErrors",
    # cache
    "GenreCache",
]
