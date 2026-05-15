"""Tests for tool_registry primitives — ToolContext, ToolResult, Registry."""

from __future__ import annotations

import dataclasses

import pytest

from sidequest.agents.tool_registry import (
    ToolCategory,
    ToolContext,
    ToolResult,
    ToolResultStatus,
)


def test_tool_result_ok_payload() -> None:
    r = ToolResult.ok({"hp": 12})
    assert r.status is ToolResultStatus.OK
    assert r.payload == {"hp": 12}
    assert r.message is None


def test_tool_result_not_found_carries_message() -> None:
    r = ToolResult.not_found("no monster named 'banana'")
    assert r.status is ToolResultStatus.NOT_FOUND
    assert r.message == "no monster named 'banana'"


def test_tool_result_error_recoverable_default() -> None:
    r = ToolResult.error("validation failed")
    assert r.status is ToolResultStatus.ERROR_RECOVERABLE
    assert r.message == "validation failed"


def test_tool_result_error_non_recoverable() -> None:
    r = ToolResult.error("db corrupt", recoverable=False)
    assert r.status is ToolResultStatus.ERROR_FATAL


def test_tool_result_to_anthropic_payload_ok() -> None:
    r = ToolResult.ok({"x": 1})
    body, is_error = r.to_anthropic_payload()
    assert is_error is False
    assert '"x"' in body


def test_tool_result_to_anthropic_payload_error_recoverable() -> None:
    r = ToolResult.error("nope")
    body, is_error = r.to_anthropic_payload()
    assert is_error is True
    assert "nope" in body


def test_tool_category_enum_values() -> None:
    assert ToolCategory.READ.value == "read"
    assert ToolCategory.WRITE.value == "write"
    assert ToolCategory.GENERATE.value == "generate"


def test_tool_context_is_frozen() -> None:
    from unittest.mock import MagicMock

    ctx = ToolContext(
        world_id="w",
        session_id="s",
        perspective_pc="alex",
        turn_number=42,
        store=MagicMock(),
        otel_span=MagicMock(),
        perception_filter=MagicMock(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.world_id = "different"  # type: ignore[misc]


def test_tool_context_perspective_pc_optional() -> None:
    from unittest.mock import MagicMock

    ctx = ToolContext(
        world_id="w",
        session_id="s",
        perspective_pc=None,
        turn_number=1,
        store=MagicMock(),
        otel_span=MagicMock(),
        perception_filter=MagicMock(),
    )
    assert ctx.perspective_pc is None
