"""Wiring tests — LocalDM runs between sealed-letter and narrator in the session handler."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.server.conftest import _build_turn_context_for_test, _make_minimal_narration_turn_result


async def test_execute_narration_turn_invokes_local_dm_before_narrator(session_fixture):
    """The session handler calls LocalDM.decompose exactly once before
    orchestrator.run_narration_turn, and attaches the result to TurnContext."""
    sd, handler = session_fixture

    captured: dict = {}
    call_order: list[str] = []

    async def fake_decompose(**kwargs):
        from sidequest.protocol.dispatch import DispatchPackage
        call_order.append("decompose")
        captured["decomposer_called"] = True
        captured["raw_action"] = kwargs["raw_action"]
        return DispatchPackage(
            turn_id=kwargs["turn_id"], per_player=[], cross_player=[],
            confidence_global=1.0, degraded=False, degraded_reason=None,
        )

    async def fake_run_narration_turn(action, context):
        call_order.append("narrator")
        captured["narrator_called"] = True
        captured["narrator_saw_dispatch_package"] = context.dispatch_package is not None
        return _make_minimal_narration_turn_result(narration="ok")

    with patch.object(sd.local_dm, "decompose", side_effect=fake_decompose), \
         patch.object(sd.orchestrator, "run_narration_turn", AsyncMock(side_effect=fake_run_narration_turn)):
        await handler._execute_narration_turn(sd, "I look around.", _build_turn_context_for_test(sd))

    assert captured["decomposer_called"] is True
    assert captured["narrator_called"] is True
    assert captured["raw_action"] == "I look around."
    assert captured["narrator_saw_dispatch_package"] is True
    # decomposer must run before the narrator
    assert call_order == ["decompose", "narrator"], f"Expected decompose→narrator, got {call_order}"


async def test_execute_narration_turn_continues_when_decomposer_degraded(session_fixture):
    """A degraded decomposer package does not abort the turn."""
    sd, handler = session_fixture

    async def degraded_decompose(**kwargs):
        from sidequest.protocol.dispatch import DispatchPackage
        return DispatchPackage(
            turn_id=kwargs["turn_id"], per_player=[], cross_player=[],
            confidence_global=0.0, degraded=True, degraded_reason="test-forced",
        )

    narrator_called = False

    async def fake_run(action, context):
        nonlocal narrator_called
        narrator_called = True
        return _make_minimal_narration_turn_result(narration="ok")

    with patch.object(sd.local_dm, "decompose", side_effect=degraded_decompose), \
         patch.object(sd.orchestrator, "run_narration_turn", AsyncMock(side_effect=fake_run)):
        await handler._execute_narration_turn(sd, "x", _build_turn_context_for_test(sd))

    assert narrator_called, "narrator must still run when decomposer is degraded"
