"""Tests for ``sidequest.game.lore_seeding`` — Story 2.3 Slice F.

Covers ``seed_lore_from_char_creation`` fragment shape + id format
(Rust parity) and ``seed_lore_from_genre_pack`` against a real loaded
pack — no synthetic GenrePack fixtures, since the aggregate root is
wide and the seeding helpers only read ``pack.lore``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.lore_seeding import (
    seed_lore_from_char_creation,
    seed_lore_from_genre_pack,
)
from sidequest.game.lore_store import (
    LoreCategory,
    LoreSource,
    LoreStore,
)
from sidequest.genre.loader import load_genre_pack
from sidequest.genre.models.character import (
    CharCreationChoice,
    CharCreationScene,
    MechanicalEffects,
)
from sidequest.genre.models.pack import GenrePack

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


def _choice(label: str, description: str) -> CharCreationChoice:
    return CharCreationChoice(
        label=label,
        description=description,
        mechanical_effects=MechanicalEffects(),
    )


def _scene(scene_id: str, choices: list[CharCreationChoice]) -> CharCreationScene:
    return CharCreationScene(
        id=scene_id,
        title=scene_id.replace("_", " ").title(),
        narration="prompt",
        choices=choices,
    )


# ---------------------------------------------------------------------------
# seed_lore_from_char_creation
# ---------------------------------------------------------------------------


class TestSeedFromCharCreation:
    def test_one_fragment_per_choice(self) -> None:
        store = LoreStore()
        scenes = [
            _scene(
                "origin",
                [
                    _choice("Exile", "Driven from home by famine."),
                    _choice("Hunter", "Raised among the marsh folk."),
                ],
            ),
            _scene(
                "vow",
                [_choice("Never again", "A promise made to the dead.")],
            ),
        ]
        added = seed_lore_from_char_creation(store, scenes)
        assert added == 3
        assert len(store) == 3

    def test_fragment_id_matches_rust_format(self) -> None:
        store = LoreStore()
        scenes = [
            _scene(
                "origin",
                [
                    _choice("Exile", "a"),
                    _choice("Hunter", "b"),
                ],
            ),
        ]
        seed_lore_from_char_creation(store, scenes)
        assert "lore_char_creation_origin_0" in store.fragments
        assert "lore_char_creation_origin_1" in store.fragments

    def test_fragment_shape(self) -> None:
        store = LoreStore()
        scenes = [_scene("origin", [_choice("Exile", "Driven from home.")])]
        seed_lore_from_char_creation(store, scenes)
        frag = store.fragments["lore_char_creation_origin_0"]
        assert frag.category == LoreCategory.Character
        assert frag.source == LoreSource.CharacterCreation
        assert frag.content == "Exile: Driven from home."
        assert frag.metadata == {
            "scene_id": "origin",
            "choice_index": "0",
            "choice_label": "Exile",
        }

    def test_scene_with_no_choices_produces_nothing(self) -> None:
        store = LoreStore()
        scenes = [_scene("display_only", [])]
        added = seed_lore_from_char_creation(store, scenes)
        assert added == 0
        assert store.is_empty()

    def test_empty_scene_list_is_noop(self) -> None:
        store = LoreStore()
        added = seed_lore_from_char_creation(store, [])
        assert added == 0

    def test_duplicate_ids_are_skipped_not_raised(self) -> None:
        """Re-seeding after a reconnect must not hard-fail. Rust uses
        ``if store.add(frag).is_ok()`` so duplicates silently drop.
        The Python port matches that contract via DuplicateLoreId catch."""
        store = LoreStore()
        scenes = [_scene("origin", [_choice("Exile", "a")])]
        first = seed_lore_from_char_creation(store, scenes)
        second = seed_lore_from_char_creation(store, scenes)
        assert first == 1
        assert second == 0
        assert len(store) == 1


# ---------------------------------------------------------------------------
# seed_lore_from_genre_pack — real caverns pack
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def caverns_pack() -> GenrePack:
    path = CONTENT_ROOT / "caverns_and_claudes"
    if not path.is_dir():
        pytest.skip(f"content pack not found at {path}")
    return load_genre_pack(path)


class TestSeedFromGenrePack:
    def test_adds_history_geography_cosmology_and_factions(
        self, caverns_pack: GenrePack
    ) -> None:
        store = LoreStore()
        added = seed_lore_from_genre_pack(store, caverns_pack)
        # caverns has non-empty history/geography/cosmology + multiple factions.
        assert added >= 3
        assert "lore_genre_history" in store.fragments
        assert "lore_genre_geography" in store.fragments
        assert "lore_genre_cosmology" in store.fragments

    def test_cosmology_bucketed_as_history(self, caverns_pack: GenrePack) -> None:
        store = LoreStore()
        seed_lore_from_genre_pack(store, caverns_pack)
        cosmology = store.fragments["lore_genre_cosmology"]
        # Rust bucket cosmology → History (seeding.rs line 47).
        assert cosmology.category == LoreCategory.History

    def test_faction_fragment_carries_name_metadata(
        self, caverns_pack: GenrePack
    ) -> None:
        store = LoreStore()
        seed_lore_from_genre_pack(store, caverns_pack)
        faction_frags = store.query_by_category(LoreCategory.Faction)
        if not faction_frags:
            pytest.skip("pack has no factions")
        frag = faction_frags[0]
        assert "faction_name" in frag.metadata
        assert frag.metadata["faction_name"]

    def test_idempotent_second_call_adds_nothing(
        self, caverns_pack: GenrePack
    ) -> None:
        store = LoreStore()
        first = seed_lore_from_genre_pack(store, caverns_pack)
        second = seed_lore_from_genre_pack(store, caverns_pack)
        assert first > 0
        assert second == 0
