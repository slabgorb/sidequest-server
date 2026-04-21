"""Shared threshold-crossing helpers (story 39-1).

Port of ``sidequest-api/crates/sidequest-game/src/thresholds.rs``.

Extracted from :mod:`sidequest.game.resource_pool` so ``ResourcePool``
(float-valued) and ``EdgePool`` (int-valued composure currency, epic 39)
can mint the same kind of LoreFragment-via-event when a pool value
crosses a named threshold downward.

Semantics
---------

- :func:`detect_crossings` returns thresholds where
  ``old > at and new <= at``. Upward transitions never fire. Landing on
  ``at`` from above fires; already being at ``at`` and holding does not.
- :func:`mint_threshold_lore` turns each crossed threshold into a
  :class:`LoreFragment` in the :attr:`LoreCategory.Event` category —
  high-relevance for narrator context selection — keyed by the
  threshold's ``event_id``. Duplicate ids are silently skipped: Rust's
  ``LoreStore.add`` returns ``Err`` (Python raises
  :class:`DuplicateLoreId`), and we log ``tracing::warn!`` /
  :func:`logging.warning` so the GM panel can still surface a
  misconfigured genre pack where two distinct thresholds share an
  event_id.
"""

from __future__ import annotations

import logging
from typing import Protocol, TypeVar, runtime_checkable

from sidequest.game.lore_store import (
    DuplicateLoreId,
    LoreCategory,
    LoreFragment,
    LoreSource,
    LoreStore,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class ThresholdAt(Protocol):
    """Protocol for threshold types that fire when a pool value crosses downward.

    Port of Rust ``trait ThresholdAt``. Implemented by
    :class:`sidequest.game.resource_pool.ResourceThreshold` (Value = float)
    and :class:`sidequest.game.creature_core.EdgeThreshold` (Value = int).
    """

    at: float
    event_id: str
    narrator_hint: str


T = TypeVar("T", bound=ThresholdAt)


def detect_crossings(
    thresholds: list[T],
    old_value: float,
    new_value: float,
) -> list[T]:
    """Return thresholds crossed by a value change (downward only).

    A threshold ``t`` is crossed when ``old_value > t.at`` and
    ``new_value <= t.at``. Port of Rust ``detect_crossings``.
    """
    return [t for t in thresholds if old_value > t.at and new_value <= t.at]


def mint_threshold_lore(
    thresholds: list[ThresholdAt],
    store: LoreStore,
    turn: int,
) -> None:
    """Mint a :class:`LoreFragment` per threshold crossing (story 16-11).

    Port of Rust ``mint_threshold_lore``. Each crossed threshold becomes
    a :class:`LoreFragment` with:

    - ``id`` = threshold ``event_id``
    - ``category`` = :attr:`LoreCategory.Event` (high relevance for
      narrator context selection)
    - ``content`` = threshold ``narrator_hint``
    - ``source`` = :attr:`LoreSource.GameEvent`
    - ``turn_created`` = ``turn``

    Duplicate ids are the idempotency path (same threshold crossing
    minted twice across reloads). :meth:`LoreStore.add` raises
    :class:`DuplicateLoreId`; we catch and emit :func:`logging.warning`
    so the GM panel can still surface a misconfigured genre pack where
    two distinct thresholds share an event_id. No error is propagated —
    a duplicate is almost always the idempotency case.
    """
    for threshold in thresholds:
        fragment = LoreFragment.new(
            id=threshold.event_id,
            category=LoreCategory.Event,
            content=threshold.narrator_hint,
            source=LoreSource.GameEvent,
            turn_created=turn,
            metadata={},
        )
        try:
            store.add(fragment)
        except DuplicateLoreId as err:
            logger.warning(
                "threshold lore minting rejected by LoreStore — usually "
                "idempotent re-mint; investigate if two thresholds share "
                "event_id=%r (turn=%d): %s",
                threshold.event_id,
                turn,
                err,
            )


__all__ = [
    "ThresholdAt",
    "detect_crossings",
    "mint_threshold_lore",
]
