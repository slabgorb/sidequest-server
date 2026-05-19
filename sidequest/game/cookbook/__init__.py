"""Beneath Sünden content cookbook (spec 2026-05-16).

Curates + filters an ingested SRD corpus along five orthogonal axes
into a deterministic RegionContentManifest. Authors zero stat blocks.
"""

from sidequest.game.cookbook.assemble import assemble_region  # noqa: E402
from sidequest.game.cookbook.compose import compose_room_prose  # noqa: E402
from sidequest.game.cookbook.loader import (  # noqa: E402
    CookbookBundle,
    CookbookValidationError,
    load_cookbook,
    validate_bundle,
)
from sidequest.game.cookbook.models import GeneratedRoomDescription  # noqa: E402

__all__ = [
    "CookbookBundle",
    "CookbookValidationError",
    "GeneratedRoomDescription",
    "assemble_region",
    "compose_room_prose",
    "load_cookbook",
    "validate_bundle",
]
