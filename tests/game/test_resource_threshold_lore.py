"""Story 42-2 (port of Story 16-11): Threshold crossings mint LoreFragments.

Port of
``sidequest-api/crates/sidequest-game/tests/resource_threshold_knownfact_story_16_11_tests.rs``.

Each Rust ``#[test]`` becomes one pytest function with the same name. The
Rust source is the behavioural contract — no idiomatic rewrites.

Skips (Design Deviations):

- ``threshold_lore_appears_in_narrator_context_selection``,
  ``threshold_lore_prioritized_when_event_category_requested``,
  ``end_to_end_patch_to_narrator_context`` — these Rust tests exercise
  ``select_lore_for_prompt``, which is not ported to Python yet. Skip
  until the narrator-context-selection slice lands.
  ``LoreStore.query_by_category`` IS ported and covers the load-bearing
  contract for 42-2 (minted fragment is retrievable in the Event category).

Contract under test:

- ``mint_threshold_lore(thresholds, store, turn)`` creates one
  ``LoreFragment`` per threshold, with
  ``id = event_id``, ``content = narrator_hint``,
  ``category = LoreCategory.Event``, ``source = LoreSource.GameEvent``,
  ``turn_created = turn``.
- ``LoreStore.add`` rejects duplicate ids; ``mint_threshold_lore``
  catches that rejection silently (per Rust ``tracing::warn!`` path)
  so repeated crossings are idempotent.
- Crossings feeding ``mint_threshold_lore`` come from both
  ``GameSnapshot.apply_resource_patch`` and
  ``GameSnapshot.apply_pool_decay``.
"""

from __future__ import annotations

from sidequest.game.lore_store import LoreCategory, LoreSource, LoreStore
from sidequest.game.resource_pool import (
    ResourcePatch,
    ResourcePatchOp,
    ResourcePool,
    ResourceThreshold,
    mint_threshold_lore,
)
from sidequest.game.session import GameSnapshot

# ---------------------------------------------------------------------------
# Test helpers — identical shape to test_resource_pool.py helpers
# ---------------------------------------------------------------------------


def make_pool_with_thresholds(
    name: str,
    current: float,
    min_: float,
    max_: float,
    thresholds: list[ResourceThreshold],
) -> ResourcePool:
    return ResourcePool(
        name=name,
        label=name,
        current=current,
        min=min_,
        max=max_,
        voluntary=True,
        decay_per_turn=0.0,
        thresholds=thresholds,
    )


def make_threshold(at: float, event_id: str, hint: str) -> ResourceThreshold:
    return ResourceThreshold(at=at, event_id=event_id, narrator_hint=hint)


def snapshot_with_pools(pools: list[ResourcePool]) -> GameSnapshot:
    snap = GameSnapshot()
    for pool in pools:
        snap.resources[pool.name] = pool
    return snap


# ===========================================================================
# AC1: apply_resource_patch crossing → LoreFragment minted
# ===========================================================================


def test_patch_crossing_threshold_mints_lore_fragment() -> None:
    pool = make_pool_with_thresholds(
        "humanity",
        50.0,
        0.0,
        100.0,
        [make_threshold(25.0, "humanity_low", "Humanity has dropped dangerously low.")],
    )
    snap = snapshot_with_pools([pool])
    lore = LoreStore()

    patch = ResourcePatch(
        resource_name="humanity",
        operation=ResourcePatchOp.Set,
        value=20.0,
    )
    result = snap.apply_resource_patch(patch)
    assert len(result.crossed_thresholds) == 1

    mint_threshold_lore(result.crossed_thresholds, lore, 5)

    assert len(lore) == 1


# ===========================================================================
# AC2: LoreFragment has event_id and narrator_hint
# ===========================================================================


def test_minted_fragment_carries_event_id_and_narrator_hint() -> None:
    threshold = make_threshold(25.0, "humanity_low", "Humanity has dropped dangerously low.")
    lore = LoreStore()

    mint_threshold_lore([threshold], lore, 10)

    results = lore.query_by_category(LoreCategory.Event)
    assert len(results) == 1
    frag = results[0]
    assert frag.id == "humanity_low"
    assert frag.content == "Humanity has dropped dangerously low."


def test_minted_fragment_source_is_game_event() -> None:
    threshold = make_threshold(10.0, "heat_critical", "Heat is at critical levels.")
    lore = LoreStore()

    mint_threshold_lore([threshold], lore, 3)

    results = lore.query_by_category(LoreCategory.Event)
    assert len(results) == 1
    assert results[0].source == LoreSource.GameEvent


# ===========================================================================
# AC3: High relevance — Event category + turn_created
# ===========================================================================


def test_minted_fragment_has_event_category_for_high_relevance() -> None:
    threshold = make_threshold(50.0, "morale_half", "Morale has fallen to half.")
    lore = LoreStore()

    mint_threshold_lore([threshold], lore, 7)

    results = lore.query_by_category(LoreCategory.Event)
    assert len(results) == 1, "Fragment must be in Event category for high relevance"


def test_minted_fragment_has_turn_created_for_recency_sorting() -> None:
    threshold = make_threshold(50.0, "morale_half", "Morale has fallen to half.")
    lore = LoreStore()

    mint_threshold_lore([threshold], lore, 42)

    results = lore.query_by_category(LoreCategory.Event)
    assert len(results) == 1
    assert results[0].turn_created == 42, "turn_created must be set for recency-based selection"


# ===========================================================================
# AC4: apply_pool_decay crossings also mint LoreFragments
# ===========================================================================


def test_decay_crossing_threshold_mints_lore_fragment() -> None:
    pool = make_pool_with_thresholds(
        "fuel",
        12.0,
        0.0,
        100.0,
        [make_threshold(10.0, "fuel_low", "Fuel reserves are running low.")],
    )
    pool.decay_per_turn = -5.0
    snap = snapshot_with_pools([pool])
    lore = LoreStore()

    crossings = snap.apply_pool_decay()
    assert len(crossings) == 1, "decay should cross the fuel_low threshold"

    mint_threshold_lore(crossings, lore, 15)

    assert len(lore) == 1
    results = lore.query_by_category(LoreCategory.Event)
    assert results[0].id == "fuel_low"
    assert results[0].content == "Fuel reserves are running low."


# ===========================================================================
# AC5: Duplicate event_id → no second fragment (idempotency)
# ===========================================================================


def test_duplicate_threshold_crossing_does_not_mint_second_fragment() -> None:
    """A crossed threshold's ``event_id`` is the LoreFragment id — a second
    mint with the same id must be silently rejected by the store (Rust
    ``LoreStore.add`` returns an error, ``mint_threshold_lore`` logs
    ``tracing::warn!`` and does not propagate)."""
    threshold = make_threshold(25.0, "humanity_low", "Humanity has dropped dangerously low.")
    lore = LoreStore()

    # First crossing — mints.
    mint_threshold_lore([threshold], lore, 5)
    assert len(lore) == 1

    # Second crossing with same event_id — must NOT add, and MUST NOT raise.
    mint_threshold_lore([threshold], lore, 10)
    assert len(lore) == 1, "duplicate event_id must not create a second fragment"


# ===========================================================================
# AC6: Multiple thresholds → multiple fragments
# ===========================================================================


def test_multiple_thresholds_crossed_mints_multiple_fragments() -> None:
    pool = make_pool_with_thresholds(
        "humanity",
        80.0,
        0.0,
        100.0,
        [
            make_threshold(75.0, "humanity_warning", "Humanity is declining."),
            make_threshold(50.0, "humanity_half", "Humanity has fallen to half."),
            make_threshold(25.0, "humanity_low", "Humanity is dangerously low."),
        ],
    )
    snap = snapshot_with_pools([pool])
    lore = LoreStore()

    patch = ResourcePatch(
        resource_name="humanity",
        operation=ResourcePatchOp.Set,
        value=20.0,
    )
    result = snap.apply_resource_patch(patch)
    assert len(result.crossed_thresholds) == 3

    mint_threshold_lore(result.crossed_thresholds, lore, 8)

    assert len(lore) == 3, "each crossed threshold should mint one fragment"

    events = lore.query_by_category(LoreCategory.Event)
    ids = [f.id for f in events]
    assert "humanity_warning" in ids
    assert "humanity_half" in ids
    assert "humanity_low" in ids
