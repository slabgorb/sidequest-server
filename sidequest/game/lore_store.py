"""In-memory indexed lore collection — Story 2.3 Slice F MVP.

Port of ``sidequest-api/crates/sidequest-game/src/lore/store.rs``
restricted to the mutation + categorical-query surface the chargen
confirmation path actually consumes:

- :class:`LoreStore.add` with duplicate-id rejection
- :meth:`LoreStore.query_by_category`
- :meth:`LoreStore.query_by_keyword` (case-insensitive substring)
- :meth:`LoreStore.total_tokens`, :meth:`LoreStore.len` accessors

Semantic / embedding search (:meth:`query_by_similarity`, embedding
retry bookkeeping, :class:`cosine_similarity`) is intentionally
deferred — the narrator runtime that consumes embeddings lands with
a later slice. Fragments carry placeholder ``embedding`` /
``embedding_pending`` fields so saved JSON round-trips with the Rust
shape, but nothing in Phase 2 reads them.

The ``metadata`` dict is string-to-string to match Rust's
``HashMap<String, String>``. Callers should not stash structured
objects there — use metadata keys for scene_id, choice_index, etc.,
not ad-hoc blobs.
"""

from __future__ import annotations

from typing import Iterable

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
    # Semantic-search fields — untouched by Phase 2; present for save
    # round-trip with future narrator-runtime slices.
    embedding: list[float] | None = None
    embedding_pending: bool = False

    @classmethod
    def new(
        cls,
        id: str,
        category: str,
        content: str,
        source: str,
        turn_created: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> "LoreFragment":
        """Build a fragment with a computed token estimate.

        Matches Rust ``LoreFragment::new`` — the token estimate is
        always derived from ``content`` length, never supplied by the
        caller. This keeps the budget-tracking math honest.
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


__all__ = [
    "DuplicateLoreId",
    "LoreCategory",
    "LoreFragment",
    "LoreSource",
    "LoreStore",
]
