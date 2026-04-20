"""Tests for ``sidequest.game.lore_store`` — Story 2.3 Slice F MVP.

Exercises the data-model + mutation + query surface ported from
``sidequest-api/crates/sidequest-game/src/lore/store.rs``.
"""

from __future__ import annotations

import pytest

from sidequest.game.lore_store import (
    DuplicateLoreId,
    LoreCategory,
    LoreFragment,
    LoreSource,
    LoreStore,
    _estimate_tokens,
)


# ---------------------------------------------------------------------------
# Token estimate
# ---------------------------------------------------------------------------


class TestTokenEstimate:
    def test_ceiling_four_chars_per_token(self) -> None:
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("a") == 1
        assert _estimate_tokens("abcd") == 1
        assert _estimate_tokens("abcde") == 2
        # 16 chars → exactly 4; 17 → 5
        assert _estimate_tokens("x" * 16) == 4
        assert _estimate_tokens("x" * 17) == 5

    def test_new_computes_estimate_from_content(self) -> None:
        frag = LoreFragment.new(
            id="x",
            category=LoreCategory.History,
            content="x" * 40,
            source=LoreSource.GenrePack,
        )
        assert frag.token_estimate == 10


# ---------------------------------------------------------------------------
# LoreFragment defaults
# ---------------------------------------------------------------------------


class TestLoreFragment:
    def test_new_defaults_are_clean(self) -> None:
        frag = LoreFragment.new(
            id="lore_x",
            category=LoreCategory.Character,
            content="Some narrative text.",
            source=LoreSource.CharacterCreation,
        )
        assert frag.id == "lore_x"
        assert frag.category == "character"
        assert frag.source == "character_creation"
        assert frag.turn_created is None
        assert frag.metadata == {}
        assert frag.embedding is None
        assert frag.embedding_pending is False

    def test_metadata_round_trips(self) -> None:
        frag = LoreFragment.new(
            id="lore_y",
            category=LoreCategory.Faction,
            content="content",
            source=LoreSource.GenrePack,
            metadata={"faction_name": "Ashgate"},
        )
        assert frag.metadata == {"faction_name": "Ashgate"}


# ---------------------------------------------------------------------------
# LoreStore mutation
# ---------------------------------------------------------------------------


class TestLoreStoreMutation:
    def test_empty_state(self) -> None:
        store = LoreStore()
        assert store.is_empty()
        assert len(store) == 0
        assert store.total_tokens() == 0

    def test_add_tracks_len_and_total_tokens(self) -> None:
        store = LoreStore()
        store.add(
            LoreFragment.new(
                id="a",
                category=LoreCategory.History,
                content="A" * 40,
                source=LoreSource.GenrePack,
            )
        )
        store.add(
            LoreFragment.new(
                id="b",
                category=LoreCategory.Geography,
                content="B" * 20,
                source=LoreSource.GenrePack,
            )
        )
        assert len(store) == 2
        assert store.total_tokens() == 10 + 5

    def test_duplicate_id_raises(self) -> None:
        store = LoreStore()
        frag = LoreFragment.new(
            id="dup",
            category=LoreCategory.History,
            content="x",
            source=LoreSource.GenrePack,
        )
        store.add(frag)
        with pytest.raises(DuplicateLoreId) as exc:
            store.add(frag)
        assert "dup" in str(exc.value)


# ---------------------------------------------------------------------------
# LoreStore queries
# ---------------------------------------------------------------------------


class TestLoreStoreQuery:
    def _three(self) -> LoreStore:
        store = LoreStore()
        store.add(
            LoreFragment.new(
                id="h",
                category=LoreCategory.History,
                content="Long ago the vault opened",
                source=LoreSource.GenrePack,
            )
        )
        store.add(
            LoreFragment.new(
                id="g",
                category=LoreCategory.Geography,
                content="The Approach crosses the Quiet Field",
                source=LoreSource.GenrePack,
            )
        )
        store.add(
            LoreFragment.new(
                id="c",
                category=LoreCategory.Character,
                content="The player grew up in ASHGATE",
                source=LoreSource.CharacterCreation,
            )
        )
        return store

    def test_query_by_category_exact_match(self) -> None:
        store = self._three()
        hist = store.query_by_category(LoreCategory.History)
        assert [f.id for f in hist] == ["h"]
        assert store.query_by_category(LoreCategory.Item) == []

    def test_query_by_keyword_is_case_insensitive_substring(self) -> None:
        store = self._three()
        got = {f.id for f in store.query_by_keyword("ashgate")}
        assert got == {"c"}

        got = {f.id for f in store.query_by_keyword("VAULT")}
        assert got == {"h"}

        got = {f.id for f in store.query_by_keyword("field")}
        assert got == {"g"}

    def test_iteration_yields_all_fragments(self) -> None:
        store = self._three()
        ids = {f.id for f in store}
        assert ids == {"h", "g", "c"}


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_store_json_round_trip(self) -> None:
        store = LoreStore()
        store.add(
            LoreFragment.new(
                id="a",
                category=LoreCategory.History,
                content="content-a",
                source=LoreSource.GenrePack,
                metadata={"k": "v"},
            )
        )
        store.add(
            LoreFragment.new(
                id="b",
                category=LoreCategory.Character,
                content="content-b",
                source=LoreSource.CharacterCreation,
            )
        )

        restored = LoreStore.model_validate_json(store.model_dump_json())
        assert len(restored) == 2
        assert restored.fragments["a"].metadata == {"k": "v"}
        assert restored.fragments["b"].source == "character_creation"
