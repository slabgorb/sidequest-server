from __future__ import annotations

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.lore_store import LoreStore
from sidequest.game.resource_pool import (
    ResourcePool,
    ResourceThreshold,
    UnknownResource,
)
from sidequest.game.session import GameSnapshot
from sidequest.server.dispatch.encounter_lifecycle import apply_resource_patches


def _pool(
    *,
    name: str,
    current: float,
    thresholds: list[ResourceThreshold] | None = None,
) -> ResourcePool:
    return ResourcePool(
        name=name,
        current=current,
        min=0.0,
        max=10.0,
        voluntary=False,
        decay_per_turn=0.0,
        thresholds=thresholds or [],
    )


def test_affinity_progress_applied_to_pool() -> None:
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.resources["Luck"] = _pool(name="Luck", current=5.0)
    crossed = apply_resource_patches(
        snap, affinity_progress=[("Luck", 3)], lore_store=LoreStore(), turn=1,
    )
    assert snap.resources["Luck"].current == 8.0
    assert crossed == []


def test_crossing_threshold_mints_lore() -> None:
    snap = GameSnapshot(genre_slug="neon_dystopia")
    threshold = ResourceThreshold(
        at=3.0,
        event_id="humanity_low",
        narrator_hint="cold eyes",
    )
    snap.resources["Humanity"] = _pool(
        name="Humanity", current=5.0, thresholds=[threshold],
    )
    store = LoreStore()
    crossed = apply_resource_patches(
        snap, affinity_progress=[("Humanity", -3)],
        lore_store=store, turn=2,
    )
    # 5 → 2 crosses the 3.0 threshold (old > at and new <= at).
    assert snap.resources["Humanity"].current == 2.0
    assert len(crossed) == 1
    assert crossed[0].event_id == "humanity_low"
    # Lore was added to the store — caller doesn't need to re-mint.
    assert store.fragments.get("humanity_low") is not None


def test_unknown_pool_name_raises() -> None:
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    with pytest.raises(UnknownResource):
        apply_resource_patches(
            snap, affinity_progress=[("Nonsense", 1)],
            lore_store=LoreStore(), turn=1,
        )


def test_empty_affinity_progress_is_noop() -> None:
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.resources["Luck"] = _pool(name="Luck", current=5.0)
    crossed = apply_resource_patches(
        snap, affinity_progress=[], lore_store=LoreStore(), turn=1,
    )
    assert crossed == []
    assert snap.resources["Luck"].current == 5.0


def test_narration_result_with_affinity_progress_applies_patches() -> None:
    """Integration: NarrationTurnResult.affinity_progress is applied to pools."""
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    # Initialize a resource pool.
    snap.resources["Luck"] = _pool(name="Luck", current=5.0)

    # Create a result with affinity_progress.
    result = NarrationTurnResult(
        narration="Luck seems to favor you.",
        affinity_progress=[("Luck", 2)],
    )

    # Apply the narration result (this calls _apply_narration_result_to_snapshot,
    # which does NOT apply resource patches — that happens in session_handler
    # _execute_narration_turn). Verify the result object carries the progress.
    assert result.affinity_progress == [("Luck", 2)]

    # Verify our helper can consume it.
    crossed = apply_resource_patches(
        snap,
        affinity_progress=result.affinity_progress or [],
        lore_store=LoreStore(),
        turn=1,
    )
    assert snap.resources["Luck"].current == 7.0
    assert crossed == []
