"""Seed a :class:`LoreStore` from genre pack + character creation data.

Called at chargen confirmation so backstory choices made during
character creation are visible to the later RAG retrieval pipeline.
Without this seed the character's narrative anchors only live on the
builder, which is discarded immediately after confirmation.

Fragment id formats:

- Genre pack: ``lore_genre_history`` / ``lore_genre_geography`` /
  ``lore_genre_cosmology`` / ``lore_genre_faction_<slug>``
- Character creation: ``lore_char_creation_<scene_id>_<choice_index>``

Duplicate ids are silently skipped — seeding is idempotent so a
reconnect that re-seeds won't hard-fail.
"""

from __future__ import annotations

from sidequest.game.lore_store import (
    DuplicateLoreId,
    LoreCategory,
    LoreFragment,
    LoreSource,
    LoreStore,
)
from sidequest.genre.models.character import CharCreationScene
from sidequest.genre.models.pack import GenrePack


def _try_add(store: LoreStore, fragment: LoreFragment) -> bool:
    """Insert ``fragment``; return ``True`` on success, ``False`` on duplicate."""
    try:
        store.add(fragment)
    except DuplicateLoreId:
        return False
    return True


def seed_lore_from_genre_pack(store: LoreStore, pack: GenrePack) -> int:
    """Seed ``store`` with fragments derived from ``pack.lore``.

    Returns the number of fragments successfully added (duplicates
    skipped).
    """
    count = 0

    if pack.lore.history:
        if _try_add(
            store,
            LoreFragment.new(
                id="lore_genre_history",
                category=LoreCategory.History,
                content=pack.lore.history,
                source=LoreSource.GenrePack,
            ),
        ):
            count += 1

    if pack.lore.geography:
        if _try_add(
            store,
            LoreFragment.new(
                id="lore_genre_geography",
                category=LoreCategory.Geography,
                content=pack.lore.geography,
                source=LoreSource.GenrePack,
            ),
        ):
            count += 1

    if pack.lore.cosmology:
        if _try_add(
            store,
            LoreFragment.new(
                id="lore_genre_cosmology",
                # Cosmology fragments bucket into the History category.
                category=LoreCategory.History,
                content=pack.lore.cosmology,
                source=LoreSource.GenrePack,
            ),
        ):
            count += 1

    for faction in pack.lore.factions:
        slug = faction.name.lower().replace(" ", "_")
        if _try_add(
            store,
            LoreFragment.new(
                id=f"lore_genre_faction_{slug}",
                category=LoreCategory.Faction,
                content=f"{faction.name}: {faction.description}",
                source=LoreSource.GenrePack,
                metadata={"faction_name": faction.name},
            ),
        ):
            count += 1

    return count


def seed_lore_from_char_creation(
    store: LoreStore, scenes: list[CharCreationScene]
) -> int:
    """Seed ``store`` with one fragment per chargen choice.

    Returns the number of fragments successfully added. Fragment ids
    follow ``lore_char_creation_<scene_id>_<choice_index>`` so saves
    survive the backend swap cleanly.
    """
    count = 0
    for scene in scenes:
        for index, choice in enumerate(scene.choices):
            fragment = LoreFragment.new(
                id=f"lore_char_creation_{scene.id}_{index}",
                category=LoreCategory.Character,
                content=f"{choice.label}: {choice.description}",
                source=LoreSource.CharacterCreation,
                metadata={
                    "scene_id": scene.id,
                    "choice_index": str(index),
                    "choice_label": choice.label,
                },
            )
            if _try_add(store, fragment):
                count += 1
    return count


__all__ = ["seed_lore_from_char_creation", "seed_lore_from_genre_pack"]
