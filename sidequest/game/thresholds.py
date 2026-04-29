"""Shared threshold-crossing helpers (story 39-1).

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
  threshold's ``event_id``. Duplicate ids raise
  :class:`DuplicateLoreId`; we log :func:`logging.warning` so the GM
  panel can still surface a misconfigured genre pack where two distinct
  thresholds share an event_id.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
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

    Implemented by :class:`sidequest.game.resource_pool.ResourceThreshold`
    (``at: float``) and :class:`sidequest.game.creature_core.EdgeThreshold`
    (``at: int``). The attribute is widened to ``int | float`` so both
    concrete threshold types satisfy the Protocol structurally — Python's
    Protocol attribute annotations are invariant, so ``at: float`` would
    reject ``EdgeThreshold.at: int`` under strict checkers.
    """

    at: int | float
    event_id: str
    narrator_hint: str


T = TypeVar("T", bound=ThresholdAt)


def detect_crossings(
    thresholds: list[T],
    old_value: int | float,
    new_value: int | float,
) -> list[T]:
    """Return thresholds crossed by a value change.

    A threshold ``t`` is crossed when:

    - ``direction == "down"`` (default): ``old_value > t.at`` and
      ``new_value <= t.at`` — value fell through the boundary.
    - ``direction == "up"``: ``old_value < t.at`` and
      ``new_value >= t.at`` — value rose through the boundary.

    ``direction`` is read via :func:`getattr` with a ``"down"`` default so
    that :class:`EdgeThreshold` (which predates this field) satisfies the
    :class:`ThresholdAt` protocol without carrying the attribute.

    Values may be ``int`` or ``float``.
    """
    fired: list[T] = []
    for t in thresholds:
        direction = getattr(t, "direction", "down")
        if (direction == "down" and old_value > t.at and new_value <= t.at) or (
            direction == "up" and old_value < t.at and new_value >= t.at
        ):
            fired.append(t)
    return fired


def mint_threshold_lore(
    thresholds: Sequence[ThresholdAt],
    store: LoreStore,
    turn: int,
) -> None:
    """Mint a :class:`LoreFragment` per threshold crossing (story 16-11).

    Each crossed threshold becomes a :class:`LoreFragment` with:

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
