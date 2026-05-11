"""ADR-098: oversized prompt logs a warning + emits OTEL but does not fail the turn."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_oversized_prompt_warns_but_completes(simple_turn_context, caplog):
    """Force the budget below realistic prompt size; assert warning + completion."""
    client = AsyncMock()
    client.send_stateless = AsyncMock(
        return_value=ClaudeResponse(text='{"narration":"ok"}', session_id=None)
    )

    orch = Orchestrator(client=client)
    with patch("sidequest.agents.orchestrator.SOFT_PROMPT_BUDGET_BYTES", 10), caplog.at_level(logging.WARNING, logger="sidequest.agents.orchestrator"):
        result = await orch._run_narration_turn_synchronous("look", simple_turn_context)

    assert result.narration

    assert any(
        "narrator.prompt_oversized" in r.message for r in caplog.records
    ), f"oversized canary did not fire; caplog: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_normal_prompt_no_canary(simple_turn_context, caplog):
    """At normal size, no canary warning fires."""
    client = AsyncMock()
    client.send_stateless = AsyncMock(
        return_value=ClaudeResponse(text='{"narration":"ok"}', session_id=None)
    )

    orch = Orchestrator(client=client)
    with caplog.at_level(logging.WARNING, logger="sidequest.agents.orchestrator"):
        await orch._run_narration_turn_synchronous("look", simple_turn_context)

    assert not any(
        "narrator.prompt_oversized" in r.message for r in caplog.records
    )
