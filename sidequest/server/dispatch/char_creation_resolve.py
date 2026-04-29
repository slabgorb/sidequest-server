"""Character-creation scene resolver — world-tier precedence over genre-tier.

Mirrors :mod:`sidequest.server.dispatch.opening_hook` and
:mod:`sidequest.server.dispatch.culture_context`: when the selected world
declares its own ``char_creation`` list, that replaces the genre-level
scenes wholesale. There is no merge; ``World.char_creation`` is a
complete scene list, not a delta. World-empty (or world-not-in-pack)
falls through to ``pack.char_creation``.

Story 45-NN: closes a wiring gap where ``World.char_creation`` was
loaded by the genre loader (``loader.py:397-402``) and present on the
``World`` model (``models/pack.py:134``) but never read — both
``CharacterBuilder`` construction sites in ``handlers/connect.py`` only
consulted the genre pack.
"""

from __future__ import annotations

from sidequest.genre.models.character import CharCreationScene
from sidequest.genre.models.pack import GenrePack


def resolve_char_creation_scenes(
    pack: GenrePack,
    world_slug: str | None,
) -> list[CharCreationScene]:
    """Return the chargen scenes for a connection.

    World-tier when ``pack.worlds[world_slug].char_creation`` is
    non-empty; otherwise genre-tier ``pack.char_creation``. The world
    list **replaces** the genre list — it is not merged. Falsy
    ``world_slug`` (``None`` or empty string) and unknown worlds both
    fall through to the genre tier.

    Returns a fresh list each call so callers can mutate freely without
    aliasing the model's stored list.
    """
    if world_slug:
        world = pack.worlds.get(world_slug)
        if world is not None and world.char_creation:
            return list(world.char_creation)
    return list(pack.char_creation)
