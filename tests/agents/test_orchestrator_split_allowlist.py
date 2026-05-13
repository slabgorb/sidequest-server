"""ADR-098: every registered section name must be on the allowlist or default to user."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.orchestrator import Orchestrator
from sidequest.agents.prompt_framework.bucket import (
    STABLE_SECTION_NAMES,
    SectionBucket,
    default_bucket_for_section,
)


@pytest.mark.asyncio
async def test_all_registered_sections_have_deterministic_bucket(simple_turn_context):
    """For a representative turn, every registered section is bucketed without surprise."""
    client = AsyncMock()
    client.send_stateless = AsyncMock(
        return_value=ClaudeResponse(text='{"narration":"ok"}', session_id=None)
    )
    orch = Orchestrator(client=client)

    _, registry = await orch.build_narrator_prompt("look around", simple_turn_context)
    section_names = {s.name for s in registry.registry(orch._narrator.name())}

    pinned = section_names & STABLE_SECTION_NAMES
    assert pinned, (
        f"None of STABLE_SECTION_NAMES appeared in the prompt for a basic turn; "
        f"registered: {sorted(section_names)}; allowlist: {sorted(STABLE_SECTION_NAMES)}"
    )

    for name in section_names:
        bucket = default_bucket_for_section(name)
        assert bucket in (SectionBucket.System, SectionBucket.User)


@pytest.mark.asyncio
async def test_known_dynamic_sections_default_to_user(simple_turn_context):
    """Spot-check: player_action, game_state, npc_roster must NOT be in system bucket."""
    client = AsyncMock()
    client.send_stateless = AsyncMock(
        return_value=ClaudeResponse(text='{"narration":"ok"}', session_id=None)
    )
    orch = Orchestrator(client=client)

    captured: dict[str, str] = {}

    async def capture(system_prompt: str, user_message: str, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_message
        return ClaudeResponse(text='{"narration":"ok"}', session_id=None)

    client.send_stateless.side_effect = capture
    await orch._run_narration_turn_synchronous("look around", simple_turn_context)

    assert "look around" in captured["user"]
    assert "look around" not in captured["system"]
