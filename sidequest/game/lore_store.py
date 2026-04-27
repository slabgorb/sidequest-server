"""In-memory indexed lore collection — Story 2.3 Slice F MVP.

Surface used by the chargen confirmation and narration-turn paths:

- :meth:`LoreStore.add` with duplicate-id rejection
- :meth:`LoreStore.query_by_category`
- :meth:`LoreStore.query_by_keyword` (case-insensitive substring)
- :meth:`LoreStore.query_by_similarity` (semantic search — story 37-33)
- :meth:`LoreStore.update_embedding` (embedding worker write-back)
- :meth:`LoreStore.total_tokens`, :meth:`LoreStore.__len__` accessors

Semantic search wires up in story 37-33 alongside
``sidequest.daemon_client.DaemonClient.embed()``. Fragments persist
their ``embedding`` vector across save/load; the embedding worker
in :mod:`sidequest.game.lore_embedding` populates them asynchronously
and the narrator RAG path reads them back via ``query_by_similarity``.

The ``metadata`` dict is string-to-string. Callers should not stash
structured objects there — use metadata keys for scene_id, choice_index,
etc., not ad-hoc blobs.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Category / source enums
# ---------------------------------------------------------------------------


class LoreCategory:
    """String constants for the :class:`LoreFragment` category tag.

    Values are snake_case so saves round-trip stably. ``Custom`` is
    represented as the raw custom label; any free-form string value here
    is treated as a custom category.
    """

    History = "history"
    Geography = "geography"
    Faction = "faction"
    Character = "character"
    Item = "item"
    Event = "event"
    Language = "language"


class LoreSource:
    """String constants for the :class:`LoreFragment` source tag."""

    GenrePack = "genre_pack"
    CharacterCreation = "character_creation"
    GameEvent = "game_event"


# ---------------------------------------------------------------------------
# LoreFragment — a single indexed piece of world knowledge
# ---------------------------------------------------------------------------


def _estimate_tokens(content: str) -> int:
    """~4 chars per token, ceiling."""
    return (len(content) + 3) // 4


class LoreFragment(BaseModel):
    """A single indexed piece of world-building knowledge."""

    model_config = {"extra": "forbid"}

    id: str
    category: str
    content: str = Field(min_length=1)
    token_estimate: int
    source: str

    @field_validator("content")
    @classmethod
    def _content_must_not_be_blank(cls, v: str) -> str:
        """Pydantic ``min_length=1`` counts characters; a whitespace-only
        string like ``"   "`` passes that check but would produce a
        degenerate embedding. Reject those too, at the construction
        boundary closest to the authoring mistake.
        """
        if not v.strip():
            raise ValueError("content must not be blank or whitespace-only")
        return v
    turn_created: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    # Semantic-search fields populated asynchronously by the embedding
    # worker in :mod:`sidequest.game.lore_embedding`. Saves round-trip
    # the vector so a replayed session can query immediately without
    # paying the embed cost again.
    embedding: list[float] | None = None
    embedding_pending: bool = True
    # Soft retry budget for transient daemon failures. The worker
    # increments on each failure; callers that want to ignore a
    # stuck fragment can gate on a threshold.
    embedding_retry_count: int = 0

    @classmethod
    def new(
        cls,
        id: str,
        category: str,
        content: str,
        source: str,
        turn_created: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> LoreFragment:
        """Build a fragment with a computed token estimate.

        The token estimate is always derived from ``content`` length,
        never supplied by the caller. This keeps the budget-tracking
        math honest. Fragments start with ``embedding_pending=True`` so
        the embedding worker picks them up on the next narration turn.
        """
        return cls(
            id=id,
            category=category,
            content=content,
            token_estimate=_estimate_tokens(content),
            source=source,
            turn_created=turn_created,
            metadata=dict(metadata or {}),
        )


# ---------------------------------------------------------------------------
# LoreStore — the in-memory index
# ---------------------------------------------------------------------------


class DuplicateLoreId(Exception):
    """Raised by :meth:`LoreStore.add` when a fragment id already exists."""


class LoreStore(BaseModel):
    """In-memory collection of :class:`LoreFragment` keyed by id.

    Save files serialize the full ``fragments`` dict; semantic-search
    bookkeeping (embeddings, pending-retry flags) round-trips untouched.
    """

    model_config = {"extra": "forbid"}

    fragments: dict[str, LoreFragment] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, fragment: LoreFragment) -> None:
        """Insert a fragment.

        Raises :class:`DuplicateLoreId` when an entry with the same id
        already exists; callers use ``try / except`` or short-circuit.
        """
        if fragment.id in self.fragments:
            raise DuplicateLoreId(f"duplicate id: {fragment.id}")
        self.fragments[fragment.id] = fragment

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query_by_category(self, category: str) -> list[LoreFragment]:
        """Return all fragments matching ``category`` (exact string match)."""
        return [f for f in self.fragments.values() if f.category == category]

    def query_by_keyword(self, keyword: str) -> list[LoreFragment]:
        """Return all fragments whose content contains ``keyword`` (case-insensitive)."""
        needle = keyword.lower()
        return [f for f in self.fragments.values() if needle in f.content.lower()]

    def query_by_similarity(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[tuple[float, LoreFragment]]:
        """Return up to ``top_k`` fragments ranked by cosine similarity
        against ``query_embedding``.

        Fragments without an embedding are skipped silently (they will
        be picked up by the embedding worker on a later turn). Fragments
        whose stored embedding has a different dimension from the query
        are *also* skipped here, but the caller is expected to have run
        :meth:`requeue_dimension_mismatched` before this call so those
        fragments get re-embedded on the current model; see that method
        for the anti-silent-orphan contract. Returned list is sorted
        descending by similarity; ties are broken by id for deterministic
        output under test.

        A zero-magnitude embedding on either side is treated as 0.0
        similarity — never a division by zero.
        """
        candidates: list[tuple[float, LoreFragment]] = []
        for frag in self.fragments.values():
            if frag.embedding is None:
                continue
            sim = cosine_similarity(query_embedding, frag.embedding)
            candidates.append((sim, frag))
        candidates.sort(key=lambda item: (-item[0], item[1].id))
        return candidates[: max(0, top_k)]

    def requeue_dimension_mismatched(self, current_dim: int) -> int:
        """Flip ``embedding_pending`` back to ``True`` for every fragment
        whose stored embedding dimension differs from ``current_dim``.

        Guards against the silent-orphan failure mode where a daemon
        model upgrade (e.g. MiniLM-384 → MiniLM-768) leaves every
        pre-upgrade fragment scoring 0.0 against every query because
        :func:`cosine_similarity` returns 0.0 on length mismatch. Without
        this re-queue, ``update_embedding`` permanently clears the
        pending flag — no log, no span attribute, no GM-panel signal.

        Returns the number of fragments that were re-queued so the
        caller can emit a ``lore.dimension_mismatch_count`` OTEL span
        attribute. Called from :func:`retrieve_lore_context` before the
        similarity query; the next post-turn worker pass picks up the
        re-queued fragments and re-embeds them on the current model.

        ``current_dim`` must be positive. A zero-or-negative value means
        the caller got a zero-length embedding from the daemon — a
        no-op return protects the store from a cascade wipe on a single
        malformed daemon reply. (DaemonClient.embed should reject zero-
        length embeddings at the boundary, but this is belt-and-braces.)
        """
        if current_dim <= 0:
            return 0
        count = 0
        for frag in self.fragments.values():
            if frag.embedding is None:
                continue
            if len(frag.embedding) != current_dim:
                frag.embedding = None
                frag.embedding_pending = True
                frag.embedding_retry_count = 0
                count += 1
        return count

    # ------------------------------------------------------------------
    # Embedding worker write-back
    # ------------------------------------------------------------------

    def update_embedding(
        self,
        fragment_id: str,
        embedding: list[float],
        *,
        expected_dim: int | None = None,
    ) -> bool:
        """Attach an embedding vector to an existing fragment.

        Clears ``embedding_pending`` and resets ``embedding_retry_count``.
        Raises ``KeyError`` if the id is unknown — silent no-op on
        missing fragments would hide a genuine bug in the worker.

        ``expected_dim`` defends against the retrieve/worker race where
        :meth:`requeue_dimension_mismatched` flips a fragment's pending
        flag back to ``True`` while the worker is mid-embed. When the
        worker's ``await client.embed()`` resumes, it would otherwise
        write back the old-dimension vector and clear the pending flag
        — undoing the re-queue. Passing ``expected_dim`` causes the
        write-back to be refused when the vector's length does not
        match the caller's current-model expectation. Returns ``True``
        on a successful write, ``False`` when the write was refused.

        If ``expected_dim`` is not supplied, the method derives the
        expectation from any already-embedded fragment in the store
        (all stored embeddings must share a dimension — if any do not,
        :meth:`requeue_dimension_mismatched` should be called first).
        This keeps callers that predate the dim-race fix correct by
        default: an inconsistent write-back is refused even when the
        worker did not explicitly track a session-level dim.
        """
        if expected_dim is None:
            expected_dim = self._current_embedding_dim()
        if expected_dim is not None and len(embedding) != expected_dim:
            return False
        frag = self.fragments[fragment_id]
        frag.embedding = list(embedding)
        frag.embedding_pending = False
        frag.embedding_retry_count = 0
        return True

    def _current_embedding_dim(self) -> int | None:
        """The dimension of the first already-embedded fragment, or
        ``None`` if no fragment has an embedding. Used by
        :meth:`update_embedding` to refuse cross-dim write-backs when
        the caller does not explicitly thread ``expected_dim``.
        """
        for frag in self.fragments.values():
            if frag.embedding is not None:
                return len(frag.embedding)
        return None

    def mark_embedding_failed(self, fragment_id: str) -> int:
        """Increment the retry counter for a fragment whose embed
        dispatch failed transiently. Returns the new count.

        Does not flip ``embedding_pending`` — the fragment stays
        queued so the next worker pass re-tries.
        """
        frag = self.fragments[fragment_id]
        frag.embedding_retry_count += 1
        return frag.embedding_retry_count

    def pending_embedding_ids(self, *, max_retries: int | None = None) -> list[str]:
        """Return ids of fragments awaiting an embedding.

        A fragment qualifies if ``embedding_pending`` is true and
        its ``embedding_retry_count`` is below ``max_retries`` (or
        ``max_retries`` is ``None``, i.e. no ceiling). Ordered by id
        so tests can rely on the sequence.
        """
        ids = [
            fid
            for fid, frag in self.fragments.items()
            if frag.embedding_pending
            and (max_retries is None or frag.embedding_retry_count < max_retries)
        ]
        return sorted(ids)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def total_tokens(self) -> int:
        """Sum of per-fragment token estimates — used for budget-aware retrieval."""
        return sum(f.token_estimate for f in self.fragments.values())

    def __len__(self) -> int:
        return len(self.fragments)

    def is_empty(self) -> bool:
        return not self.fragments

    def fragments_iter(self) -> Iterator[LoreFragment]:
        """Iterate the store's fragments in insertion order.

        Prefer this over ``for frag in store`` — Pydantic v1's
        ``BaseModel.__iter__`` yields ``(field_name, value)`` tuples,
        so overriding it to yield :class:`LoreFragment` instances is a
        Liskov violation that requires a ``type: ignore``. Keeping the
        method under its own name avoids the override entirely.
        """
        return iter(self.fragments.values())


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length embedding vectors.

    Returns ``0.0`` rather than raising when either side has zero
    magnitude (uninitialized embedding, all-zero sentinel). Returns
    ``0.0`` when the lengths differ — a length mismatch indicates a
    model change across saved sessions. The query-time caller
    (:func:`retrieve_lore_context`) runs
    :meth:`LoreStore.requeue_dimension_mismatched` first so mismatched
    fragments get re-queued for the embedding worker rather than being
    permanently orphaned by this 0.0 return.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    mag_a = 0.0
    mag_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        mag_a += x * x
        mag_b += y * y
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (math.sqrt(mag_a) * math.sqrt(mag_b))


__all__ = [
    "DuplicateLoreId",
    "LoreCategory",
    "LoreFragment",
    "LoreSource",
    "LoreStore",
    "cosine_similarity",
]
