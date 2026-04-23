"""Tests for LocalDM — Group B decomposer MVP.

Task 2: stub returning an empty DispatchPackage.
Later tasks layer Haiku-backed resolution on top.
"""
from __future__ import annotations

import pytest

from sidequest.agents.local_dm import LocalDM
from sidequest.protocol.dispatch import DispatchPackage


@pytest.mark.asyncio
async def test_local_dm_stub_returns_empty_dispatch_package():
    """Task 2 — structural stub. Real body lands in Task 3.

    Stub deliberately reports degraded=True to avoid masking the stub path
    as real decomposer output during the interval between Task 2 and Task 3.
    """
    dm = LocalDM()
    pkg = await dm.decompose(
        turn_id="turn-001",
        player_id="player:Alice",
        raw_action="I look around.",
        state_summary="You stand in a tavern.",
    )
    assert isinstance(pkg, DispatchPackage)
    assert pkg.turn_id == "turn-001"
    assert pkg.per_player == []
    assert pkg.cross_player == []
    assert pkg.degraded is True
    assert pkg.degraded_reason == "stub_not_yet_implemented"
    assert pkg.confidence_global == 0.0


def test_local_dm_importable_from_package_root():
    """Wiring check — LocalDM is re-exported from sidequest.agents so other
    layers can use the package-root import style."""
    from sidequest.agents import LocalDM as LocalDMFromRoot
    assert LocalDMFromRoot is LocalDM  # same class, not a re-wrapped stub
