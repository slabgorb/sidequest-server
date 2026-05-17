"""Beneath Sünden Plan 7 Task 6 — MANDATORY WIRING TEST.

CLAUDE.md "Every Test Suite Needs a Wiring Test": unit tests prove a
component works in isolation; that is not enough. This file proves the
materializer's look-ahead frontier seam is **invoked from the real
production region-transition path** — not called directly by a test.

The real production region-transition point that mutates
``snap.current_region`` mid-session is the ADR-011 ``WorldStatePatch``
apply in ``GameSnapshot.apply_world_patch`` (session.py) — the narrator's
escape-hatch / monster-manual-inject path both flow through it
(``sidequest/agents/tools/apply_world_patch.py``,
``sidequest/server/dispatch/monster_manual_inject.py``). ADR-055's
``room_movement`` runtime surface (``validate_room_transition`` /
``apply_validated_move``) is explicitly deferred to a later story
(``room_movement.py`` docstring) and is ROOM-level, orthogonal to the
REGION-transition the materializer's frontier needs. So the
frontier-approach/crossing hook lands on the real ``apply_world_patch``
region-transition point, extending it + ADR-055 ``region_init``
semantics — NOT a parallel navigation path, NOT a stub.

Task 6 owns the producer side (the real dispatch seam wired into the real
path + promote-to-active recognition). Task 7 owns the async worker that
consumes the enqueue — this test spies the seam, it does not implement
the worker.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _restore_frontier_observers() -> Any:
    """Belt-and-suspenders: unconditionally restore
    ``frontier_hook._OBSERVERS`` after every test in this module,
    regardless of how each test wrapped registration.

    ``_OBSERVERS`` is a process-global module list. The per-test
    register/unregister ``try/finally`` blocks are the intentional,
    in-test contract and stay as-is — but a future edit (a registered
    callable that raises before its ``finally``, or a new test that
    forgets the ``finally``) would leak an observer into the global
    registry and pollute the rest of the ~6500-test suite for the whole
    process. This fixture closes that latent cross-test-pollution hole by
    snapshotting the module list before the test and restoring its exact
    contents in teardown — independent of any per-test cleanup. It reuses
    the existing module list directly (test-only snapshot/restore; no new
    production API is added just for the test).
    """
    from sidequest.dungeon import frontier_hook

    before = list(frontier_hook._OBSERVERS)
    try:
        yield
    finally:
        # Restore exact pre-test contents in place (other code may hold a
        # reference to the same list object — mutate, don't rebind).
        frontier_hook._OBSERVERS[:] = before


def test_apply_world_patch_region_transition_fires_frontier_hook() -> None:
    """Drive the REAL production region-transition (a WorldStatePatch with
    ``current_region`` applied through ``GameSnapshot.apply_world_patch``,
    the exact code path the narrator/monster-manual-inject use) and assert
    the materializer's frontier look-ahead seam was invoked FROM that path
    — observed via a real spy registered on the enqueue seam, NOT by the
    test calling the hook directly.

    If this fails because ``apply_world_patch`` does not reach the hook,
    the wiring is broken (the seam exists but production never crosses it
    — CLAUDE.md "Verify Wiring, Not Just Existence")."""
    from sidequest.dungeon.frontier_hook import (
        register_frontier_observer,
        unregister_frontier_observer,
    )
    from sidequest.game.session import GameSnapshot, WorldStatePatch

    # A real GameSnapshot at a real region (the surface entrance).
    snap = GameSnapshot(genre_slug="caverns_and_claudes", world_slug="beneath_sunden")
    snap.current_region = "entrance"

    seen: list[dict[str, Any]] = []

    def _spy(*, snapshot: Any, from_region: str | None, to_region: str) -> None:
        # The real Task-7-shaped observer signature; here it just records
        # that the production transition reached the seam.
        seen.append(
            {
                "snapshot_is": snapshot is snap,
                "from_region": from_region,
                "to_region": to_region,
            }
        )

    register_frontier_observer(_spy)
    try:
        # THE PRODUCTION PATH: a narrator-shaped WorldStatePatch moving the
        # party to a new region, applied through the real public
        # apply_world_patch entry point (NOT a direct call to the hook).
        snap.apply_world_patch(WorldStatePatch(current_region="exp001.r0"))
    finally:
        unregister_frontier_observer(_spy)

    # The seam fired BECAUSE the production region-transition fired.
    assert seen, (
        "the frontier look-ahead seam was NOT invoked from the real "
        "apply_world_patch region-transition path — the hook is not wired "
        "into production (CLAUDE.md: half-wired features are forbidden)"
    )
    assert seen[0]["snapshot_is"], "hook did not receive the live snapshot"
    assert seen[0]["from_region"] == "entrance"
    assert seen[0]["to_region"] == "exp001.r0"
    # The transition actually happened (real apply, not intercepted away).
    assert snap.current_region == "exp001.r0"


def test_region_transition_without_change_does_not_fire_hook() -> None:
    """A WorldStatePatch that does NOT set ``current_region`` (or sets it
    to the same region) must not fire the frontier seam — the hook is a
    REGION-TRANSITION hook, not a fire-on-every-patch hook (No Silent
    Fallbacks: no spurious look-ahead enqueue)."""
    from sidequest.dungeon.frontier_hook import (
        register_frontier_observer,
        unregister_frontier_observer,
    )
    from sidequest.game.session import GameSnapshot, WorldStatePatch

    snap = GameSnapshot(genre_slug="caverns_and_claudes", world_slug="beneath_sunden")
    snap.current_region = "entrance"

    fired: list[Any] = []
    obs = lambda **kw: fired.append(kw)  # noqa: E731

    register_frontier_observer(obs)
    try:
        # No current_region in the patch → no region transition.
        snap.apply_world_patch(WorldStatePatch(atmosphere="cold"))
        assert fired == [], "hook fired on a non-region-transition patch"

        # current_region set to the SAME region → not a transition.
        snap.apply_world_patch(WorldStatePatch(current_region="entrance"))
        assert fired == [], (
            "hook fired when current_region was set to the unchanged "
            "value — that is not a region transition"
        )
    finally:
        unregister_frontier_observer(obs)


def test_frontier_crossing_promotes_region_to_active() -> None:
    """The frontier-crossing transition: when the party crosses INTO a
    region, the minimal real promote-to-active state transition runs —
    the crossed-into region is recognized in ``snap.discovered_regions``
    (extending ADR-055 ``region_init`` dedup-append semantics, NOT a
    parallel path). The committed expansion is already live from Task 6's
    commit txn; "promote to active" = the session/region state now
    recognizes it."""
    from sidequest.game.session import GameSnapshot, WorldStatePatch

    snap = GameSnapshot(genre_slug="caverns_and_claudes", world_slug="beneath_sunden")
    snap.current_region = "entrance"
    snap.discovered_regions = ["entrance"]

    snap.apply_world_patch(WorldStatePatch(current_region="exp001.r0"))

    assert snap.current_region == "exp001.r0"
    assert "exp001.r0" in snap.discovered_regions, (
        "crossing into a look-ahead-materialized region did not promote "
        "it to active (it must be recognized in discovered_regions — the "
        "ADR-055 region_init dedup-append semantics, extended)"
    )
    # Dedup-append: the existing entry order is preserved (save compat,
    # the region_init contract).
    assert snap.discovered_regions[0] == "entrance"
    assert snap.discovered_regions.count("exp001.r0") == 1
