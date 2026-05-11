"""ADR-098: transient retry-once; otherwise degraded result."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.claude_client import TimeoutError as _ClaudeTimeoutError
from sidequest.agents.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_transient_failure_retries_once_then_succeeds(simple_turn_context):
    """One _ClaudeTimeoutError on first call → retry → success on second."""
    client = AsyncMock()
    call_count = {"n": 0}

    async def flaky(system_prompt, user_message, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _ClaudeTimeoutError(30.0)
        return ClaudeResponse(text='{"narration":"ok"}', session_id=None)

    client.send_stateless = AsyncMock(side_effect=flaky)
    orch = Orchestrator(client=client)

    result = await orch._run_narration_turn_synchronous("look", simple_turn_context)
    assert call_count["n"] == 2
    assert result.narration  # non-empty success


@pytest.mark.asyncio
async def test_double_transient_returns_degraded_result(simple_turn_context):
    """Two _ClaudeTimeoutError in a row → degraded in-fiction stall, no third call."""
    client = AsyncMock()
    call_count = {"n": 0}

    async def always_flaky(system_prompt, user_message, **kwargs):
        call_count["n"] += 1
        raise _ClaudeTimeoutError(30.0)

    client.send_stateless = AsyncMock(side_effect=always_flaky)
    orch = Orchestrator(client=client)

    result = await orch._run_narration_turn_synchronous("look", simple_turn_context)
    assert call_count["n"] == 2  # not 3 — no exponential retry
    assert "world holds its breath" in result.narration.lower()
