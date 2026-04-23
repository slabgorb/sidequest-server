"""Tests for distinctive_detail_hint subsystem (spec §6.2)."""
from __future__ import annotations

import pytest

from sidequest.agents.subsystems.distinctive_detail import run_distinctive_detail
from sidequest.protocol.dispatch import SubsystemDispatch, VisibilityTag


def _tag_all() -> VisibilityTag:
    return VisibilityTag(
        visible_to="all", perception_fidelity={}, secrets_for=[],
        redact_from_narrator_canonical=False,
    )


@pytest.mark.asyncio
async def test_distinctive_detail_emits_single_narrator_directive():
    dispatch = SubsystemDispatch(
        subsystem="distinctive_detail_hint",
        params={"target": "npc:goblin_2", "hint": "broken tooth"},
        depends_on=[],
        idempotency_key="idem:a",
        visibility=_tag_all(),
    )
    directives = await run_distinctive_detail(dispatch)
    assert len(directives) == 1
    d = directives[0]
    assert d.kind == "distinctive_detail_for_referent"
    assert "npc:goblin_2" in d.payload
    assert "broken tooth" in d.payload


@pytest.mark.asyncio
async def test_distinctive_detail_raises_on_missing_target():
    dispatch = SubsystemDispatch(
        subsystem="distinctive_detail_hint",
        params={"hint": "broken tooth"},  # missing target
        depends_on=[],
        idempotency_key="idem:b",
        visibility=_tag_all(),
    )
    with pytest.raises(ValueError, match="target"):
        await run_distinctive_detail(dispatch)


@pytest.mark.asyncio
async def test_distinctive_detail_raises_on_missing_hint():
    dispatch = SubsystemDispatch(
        subsystem="distinctive_detail_hint",
        params={"target": "npc:goblin_2"},  # missing hint
        depends_on=[],
        idempotency_key="idem:c",
        visibility=_tag_all(),
    )
    with pytest.raises(ValueError, match="hint"):
        await run_distinctive_detail(dispatch)
