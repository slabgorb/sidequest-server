"""In-memory indexed lore collection — Story 2.3 Slice F MVP.

Port of ``sidequest-api/crates/sidequest-game/src/lore/store.rs``
covering the mutation + query surface the chargen confirmation and
narration-turn paths consume:

- :meth:`LoreStore.add` with duplicate-id rejection
- :meth:`LoreStore.query_by_category`
- :meth:`LoreStore.query_by_keyword` (case-insensitive substring)
- :meth:`LoreStore.query_by_similarity` (semantic search — story 37-33)
- :meth:`LoreStore.update_embedding` (embedding worker write-back)
- :meth:`LoreStore.total_tokens`, :meth:`LoreStore.len` accessors

Semantic search wires up in story 37-33 alongside
``sidequest.daemon_client.DaemonClient.embed()``. Fragments persist
their ``embedding`` vector across save/load; the embedding worker
in :mod:`sidequest.game.lore_embedding` populates them asynchronously
and the narrator RAG path reads them back via ``query_by_similarity``.

The ``metadata`` dict is string-to-string to match Rust's
``HashMap<String, String>``. Callers should not stash structured
objects there — use metadata keys for scene_id, choice_index, etc.,
not ad-hoc blobs.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Category / source enums — match Rust string serialization
# ---------------------------------------------------------------------------


class LoreCategory:
    """String constants for the :class:`LoreFragment` category tag.

    Values match Rust ``#[serde(rename_all = \"snake_case\")]`` output so
    saves round-trip across backends. ``Custom`` is represented as the
    raw custom label; a free-form string value on this set is treated
    as a custom category (Rust's ``LoreCategory::Custom(String)``).
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
    """~4 chars per token, ceiling — mirrors Rust ``content.len().div_ceil(4)``."""
    return (len(content) + 3) // 4


class LoreFragment(BaseModel):
    """A single indexed piece of world-building knowledge."""

    model_config = {"extra": "forbid"}

    id: str
    category: str
    content: str
    token_estimate: int
    source: str
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

        Matches Rust ``LoreFragment::new`` — the token estimate is
        always derived from ``content`` length, never supplied by the
        caller. This keeps the budget-tracking math honest. Fragments
        start with ``embedding_pending=True`` so the embedding worker
        will pick them up on the next narration turn.
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

    Matches the Rust ``LoreStore`` mutation + query surface for the
    chargen confirmation path. Save files serialize the full
    ``fragments`` dict; semantic-search bookkeeping (embeddings,
    pending-retry flags) round-trips untouched.
    """

    model_config = {"extra": "forbid"}

    fragments: dict[str, LoreFragment] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, fragment: LoreFragment) -> None:
        """Insert a fragment. Raises on duplicate id.

        The Rust signature returns ``Result<(), String>``; the Python
        port raises :class:`DuplicateLoreId` so callers use idiomatic
        ``try / except`` or short-circuit at the call site.
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
        be picked up by the embedding worker on a later turn). Returned
        list is sorted descending by similarity; ties are broken by id
        for deterministic output under test.

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

    # ------------------------------------------------------------------
    # Embedding worker write-back
    # ------------------------------------------------------------------

    def update_embedding(self, fragment_id: str, embedding: list[float]) -> None:
        """Attach an embedding vector to an existing fragment.

        Clears ``embedding_pending`` and resets ``embedding_retry_count``.
        Raises ``KeyError`` if the id is unknown — silent no-op on
        missing fragments would hide a genuine bug in the worker.
        """
        frag = self.fragments[fragment_id]
        frag.embedding = list(embedding)
        frag.embedding_pending = False
        frag.embedding_retry_count = 0

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

    def __iter__(self) -> Iterable[LoreFragment]:  # type: ignore[override]
        return iter(self.fragments.values())


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length embedding vectors.

    Returns ``0.0`` rather than raising when either side has zero
    magnitude (uninitialized embedding, all-zero sentinel). Returns
    ``0.0`` when the lengths differ — a length mismatch indicates
    a model change across saved sessions and should degrade silently
    at query time (the worker re-embeds on the current model).
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
