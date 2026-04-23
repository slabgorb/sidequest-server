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
    cosine_similarity,
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
        # Story 37-33: fragments start pending so the embedding worker
        # can pick them up on the next narration turn.
        assert frag.embedding_pending is True
        assert frag.embedding_retry_count == 0

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
        ids = {f.id for f in store.fragments_iter()}
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


# ---------------------------------------------------------------------------
# Embedding lifecycle — Story 37-33
# ---------------------------------------------------------------------------


def _frag(id_: str, content: str = "c") -> LoreFragment:
    return LoreFragment.new(
        id=id_,
        category=LoreCategory.History,
        content=content,
        source=LoreSource.GenrePack,
    )


class TestEmbeddingLifecycle:
    def test_update_embedding_clears_pending_and_zeroes_retry_count(self) -> None:
        store = LoreStore()
        frag = _frag("a")
        store.add(frag)
        store.mark_embedding_failed("a")
        assert store.fragments["a"].embedding_retry_count == 1

        store.update_embedding("a", [0.1, 0.2, 0.3])
        assert store.fragments["a"].embedding == [0.1, 0.2, 0.3]
        assert store.fragments["a"].embedding_pending is False
        assert store.fragments["a"].embedding_retry_count == 0

    def test_update_embedding_raises_on_unknown_id(self) -> None:
        store = LoreStore()
        with pytest.raises(KeyError):
            store.update_embedding("nope", [0.1])

    def test_mark_embedding_failed_keeps_pending_true(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))

        assert store.mark_embedding_failed("a") == 1
        assert store.mark_embedding_failed("a") == 2
        assert store.fragments["a"].embedding_pending is True

    def test_pending_embedding_ids_respects_max_retries(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        store.add(_frag("b"))
        store.mark_embedding_failed("a")
        store.mark_embedding_failed("a")
        store.mark_embedding_failed("a")  # a.retry_count = 3

        assert store.pending_embedding_ids(max_retries=None) == ["a", "b"]
        assert store.pending_embedding_ids(max_retries=3) == ["b"]
        assert store.pending_embedding_ids(max_retries=10) == ["a", "b"]

    def test_pending_embedding_ids_excludes_already_embedded(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        store.add(_frag("b"))
        store.update_embedding("a", [1.0, 0.0])

        assert store.pending_embedding_ids() == ["b"]


# ---------------------------------------------------------------------------
# Cosine similarity — Story 37-33
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self) -> None:
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(
            1.0
        )

    def test_orthogonal_vectors_score_zero(self) -> None:
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_score_negative_one(self) -> None:
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_magnitude_returns_zero_not_nan(self) -> None:
        # Guards against a division-by-zero that would leak NaN into
        # the ranking list.
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert cosine_similarity([1.0, 1.0], [0.0, 0.0]) == 0.0

    def test_mismatched_lengths_return_zero(self) -> None:
        # A length change across a model swap must not raise — the
        # caller sees similarity 0.0 and the fragment drops out of
        # the top-k silently, ready for the worker to re-embed.
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_empty_vectors_return_zero(self) -> None:
        assert cosine_similarity([], []) == 0.0
        assert cosine_similarity([1.0], []) == 0.0


# ---------------------------------------------------------------------------
# Semantic search — Story 37-33
# ---------------------------------------------------------------------------


class TestQueryBySimilarity:
    def test_ranks_by_cosine_and_respects_top_k(self) -> None:
        store = LoreStore()
        store.add(_frag("close", content="close-frag"))
        store.add(_frag("mid", content="mid-frag"))
        store.add(_frag("far", content="far-frag"))
        store.update_embedding("close", [1.0, 0.0])
        store.update_embedding("mid", [0.7, 0.7])
        store.update_embedding("far", [0.0, 1.0])

        hits = store.query_by_similarity([1.0, 0.0], top_k=2)
        assert [frag.id for _, frag in hits] == ["close", "mid"]
        assert hits[0][0] == pytest.approx(1.0)

    def test_skips_fragments_without_embeddings(self) -> None:
        store = LoreStore()
        store.add(_frag("embedded"))
        store.add(_frag("bare"))
        store.update_embedding("embedded", [1.0, 0.0])

        hits = store.query_by_similarity([1.0, 0.0], top_k=5)
        assert [frag.id for _, frag in hits] == ["embedded"]

    def test_top_k_zero_returns_empty(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        store.update_embedding("a", [1.0, 0.0])
        assert store.query_by_similarity([1.0, 0.0], top_k=0) == []

    def test_tie_broken_by_id_deterministically(self) -> None:
        store = LoreStore()
        store.add(_frag("b"))
        store.add(_frag("a"))
        store.update_embedding("a", [1.0, 0.0])
        store.update_embedding("b", [1.0, 0.0])

        hits = store.query_by_similarity([1.0, 0.0], top_k=2)
        assert [frag.id for _, frag in hits] == ["a", "b"]


# ---------------------------------------------------------------------------
# Round-5: requeue_dimension_mismatched + update_embedding(expected_dim)
# ---------------------------------------------------------------------------


class TestRequeueDimensionMismatched:
    """Dimension-drift defence (Story 37-33 round-trip #4 HIGH fix)."""

    def test_no_fragments_no_requeue(self) -> None:
        store = LoreStore()
        assert store.requeue_dimension_mismatched(384) == 0

    def test_matching_dim_is_untouched(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        store.update_embedding("a", [1.0, 0.0])
        assert store.requeue_dimension_mismatched(2) == 0
        assert store.fragments["a"].embedding == [1.0, 0.0]
        assert store.fragments["a"].embedding_pending is False

    def test_mismatched_dim_resets_fragment_state(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        store.update_embedding("a", [1.0, 0.0, 0.5])  # 3-d
        store.add(_frag("b"))
        store.update_embedding("b", [0.0, 1.0, 0.5])  # 3-d
        # New model is 2-d; both should be requeued.
        assert store.requeue_dimension_mismatched(2) == 2
        assert store.fragments["a"].embedding is None
        assert store.fragments["a"].embedding_pending is True
        assert store.fragments["a"].embedding_retry_count == 0
        assert store.fragments["b"].embedding_pending is True

    def test_mixed_dims_only_mismatched_requeued(self) -> None:
        # Seed mixed-dim state directly (the store's implicit dim guard
        # on update_embedding would otherwise refuse the cross-dim
        # write, which is exactly the invariant we tested elsewhere).
        # In production, mixed-dim state only arises from a save-file
        # that predates this fix.
        store = LoreStore()
        store.add(_frag("match"))
        store.fragments["match"].embedding = [1.0, 0.0]
        store.fragments["match"].embedding_pending = False
        store.add(_frag("mismatch"))
        store.fragments["mismatch"].embedding = [1.0, 0.0, 0.5]
        store.fragments["mismatch"].embedding_pending = False
        assert store.requeue_dimension_mismatched(2) == 1
        assert store.fragments["match"].embedding == [1.0, 0.0]
        assert store.fragments["mismatch"].embedding is None
        assert store.fragments["mismatch"].embedding_pending is True

    def test_none_embedding_is_ignored(self) -> None:
        store = LoreStore()
        store.add(_frag("pending"))  # embedding=None, pending=True
        assert store.requeue_dimension_mismatched(2) == 0
        assert store.fragments["pending"].embedding_pending is True

    def test_zero_dim_is_a_no_op_belt_and_braces(self) -> None:
        """``current_dim <= 0`` must not cascade-wipe the store."""
        store = LoreStore()
        store.add(_frag("a"))
        store.update_embedding("a", [1.0, 0.0])
        assert store.requeue_dimension_mismatched(0) == 0
        assert store.fragments["a"].embedding == [1.0, 0.0]
        assert store.fragments["a"].embedding_pending is False

    def test_negative_dim_is_a_no_op(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        store.update_embedding("a", [1.0, 0.0])
        assert store.requeue_dimension_mismatched(-3) == 0
        assert store.fragments["a"].embedding_pending is False

    def test_retry_count_is_reset_on_requeue(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        store.update_embedding("a", [1.0, 0.0, 0.5])
        store.mark_embedding_failed("a")  # even though not pending
        store.mark_embedding_failed("a")
        assert store.fragments["a"].embedding_retry_count == 2
        store.requeue_dimension_mismatched(2)
        assert store.fragments["a"].embedding_retry_count == 0


class TestUpdateEmbeddingDimGuard:
    """update_embedding(expected_dim=) refuses race-undoes (Story 37-33 round-5)."""

    def test_explicit_expected_dim_match_accepts(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        written = store.update_embedding("a", [1.0, 0.0], expected_dim=2)
        assert written is True
        assert store.fragments["a"].embedding == [1.0, 0.0]
        assert store.fragments["a"].embedding_pending is False

    def test_explicit_expected_dim_mismatch_refuses(self) -> None:
        store = LoreStore()
        store.add(_frag("a"))
        # Fragment stays pending; embedding not written.
        written = store.update_embedding("a", [1.0, 0.0, 0.5], expected_dim=2)
        assert written is False
        assert store.fragments["a"].embedding is None
        assert store.fragments["a"].embedding_pending is True

    def test_implicit_dim_inferred_from_store(self) -> None:
        """When expected_dim is None, infer from any already-embedded fragment."""
        store = LoreStore()
        store.add(_frag("existing"))
        store.update_embedding("existing", [1.0, 0.0])  # establishes dim=2
        store.add(_frag("new"))
        # Mismatched write should be refused without the caller threading
        # expected_dim — the store knows its own current dim.
        assert store.update_embedding("new", [0.5, 0.5, 0.5]) is False
        assert store.fragments["new"].embedding is None
        assert store.fragments["new"].embedding_pending is True

    def test_empty_store_implicit_dim_accepts_any(self) -> None:
        """No pre-existing embeddings → no dim constraint."""
        store = LoreStore()
        store.add(_frag("first"))
        assert store.update_embedding("first", [0.1, 0.2, 0.3]) is True
        assert store.fragments["first"].embedding == [0.1, 0.2, 0.3]


class TestLoreFragmentContentValidation:
    """LoreFragment.content rejects empty + whitespace at construction."""

    def test_empty_content_raises(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LoreFragment.new(
                id="x",
                category="history",
                content="",
                source="genre_pack",
            )

    def test_whitespace_only_content_raises(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LoreFragment.new(
                id="x",
                category="history",
                content="   \t\n",
                source="genre_pack",
            )

    def test_leading_trailing_whitespace_preserved(self) -> None:
        """Non-blank content with surrounding whitespace is accepted verbatim."""
        frag = LoreFragment.new(
            id="x",
            category="history",
            content="  real content  ",
            source="genre_pack",
        )
        assert frag.content == "  real content  "
