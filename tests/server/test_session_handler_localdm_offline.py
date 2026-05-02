"""Wiring test: LocalDM is no longer on the live turn path.

If a future change re-introduces ``await sd.local_dm.decompose(...)`` on
the critical path, this test fails loud. The dormant code in
``sidequest/agents/local_dm.py`` MUST NOT be invoked during a live turn.

Expected state at end of Phase 1 (before Phase 2 lands): **RED**.
Phase 2 removes the LocalDM call from ``_execute_narration_turn``; at
that point this test goes GREEN and stays green.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sidequest.agents.local_dm import LocalDM
from tests.server.conftest import (
    _build_turn_context_for_test,
    _make_minimal_narration_turn_result,
)


@pytest.mark.asyncio
async def test_live_turn_does_not_invoke_local_dm(
    monkeypatch: pytest.MonkeyPatch,
    session_fixture,
) -> None:
    """Patch LocalDM.decompose to raise. Run a turn. Assert it succeeds.

    Phase 1 expected result: FAIL — LocalDM.decompose IS called, so the
    AssertionError fires.  Phase 2 makes this PASS by removing the call.
    """
    sd, handler = session_fixture

    async def _async_explode(*args, **kwargs):
        raise AssertionError("LocalDM.decompose was called on the live turn")

    # Patch the class method so any instance (including sd.local_dm) raises.
    monkeypatch.setattr(LocalDM, "decompose", _async_explode)

    # Fake narrator so the test doesn't stall on a real Opus subprocess.
    async def fake_run_narration_turn(action, context):
        return _make_minimal_narration_turn_result(narration="ok")

    with patch.object(
        sd.orchestrator,
        "run_narration_turn",
        AsyncMock(side_effect=fake_run_narration_turn),
    ):
        # Turn completed without invoking LocalDM — frames returned.
        frames = await handler._execute_narration_turn(
            sd,
            "I look around.",
            _build_turn_context_for_test(sd),
        )

    assert frames, "turn handler returned no frames"
