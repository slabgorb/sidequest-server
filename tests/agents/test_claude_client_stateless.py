"""Tests for the stateless send path used by the narrator (ADR-098)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from sidequest.agents.claude_client import ClaudeClient


def _build_client() -> ClaudeClient:
    """Build a client with all subprocess deps mocked at construction."""
    return ClaudeClient()


def test_send_stateless_does_not_pass_resume_or_session_id():
    """The subprocess argv must contain neither --resume nor --session-id.

    The whole point of dropping --resume: no Anthropic session is ever
    referenced or established by this code path.
    """
    client = _build_client()
    captured_args: list[str] = []

    async def fake_run(args, env, span):
        captured_args[:] = list(args)
        return _stub_response()

    with patch.object(ClaudeClient, "_run_subprocess", new=AsyncMock(side_effect=fake_run)):
        asyncio.run(
            client.send_stateless(
                system_prompt="scaffold here",
                user_message="player action here",
                model="opus",
            )
        )

    assert "--resume" not in captured_args
    assert "--session-id" not in captured_args


def test_send_stateless_includes_system_prompt_flag():
    """The stable scaffold rides on --system-prompt; the user message on -p."""
    client = _build_client()
    captured_args: list[str] = []

    async def fake_run(args, env, span):
        captured_args[:] = list(args)
        return _stub_response()

    with patch.object(ClaudeClient, "_run_subprocess", new=AsyncMock(side_effect=fake_run)):
        asyncio.run(
            client.send_stateless(
                system_prompt="SCAFFOLD",
                user_message="USER",
                model="opus",
            )
        )

    assert "--system-prompt" in captured_args
    sys_idx = captured_args.index("--system-prompt")
    assert captured_args[sys_idx + 1] == "SCAFFOLD"
    p_idx = captured_args.index("-p")
    assert captured_args[p_idx + 1] == "USER"


def test_send_stateless_raises_on_empty_user_message():
    """Empty user message is a programmer error, not a stallable turn."""
    from sidequest.agents.claude_client import EmptyResponse

    client = _build_client()
    with pytest.raises(EmptyResponse):
        asyncio.run(
            client.send_stateless(
                system_prompt="anything",
                user_message="   ",
                model="opus",
            )
        )


def _stub_response():
    """Minimal ClaudeResponse-shaped stub for the mocked subprocess path."""
    from sidequest.agents.claude_client import ClaudeResponse

    return ClaudeResponse(text="ok", session_id=None)
