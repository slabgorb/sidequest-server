"""Tests for PerceptionFilter Protocol + Noop default."""

from __future__ import annotations

from sidequest.agents.perception_filter import (
    NoopPerceptionFilter,
    PerceptionFilter,
)
from sidequest.agents.tool_registry import ToolCategory, ToolResult


def test_noop_perception_filter_passes_payload_through() -> None:
    f = NoopPerceptionFilter()
    result = ToolResult.ok({"hp": 17})
    filtered = f.filter_result(
        tool_name="query_character",
        category=ToolCategory.READ,
        result=result,
        perspective_pc="alex",
    )
    assert filtered.payload == {"hp": 17}


def test_noop_perception_filter_passes_through_when_pc_none() -> None:
    f = NoopPerceptionFilter()
    result = ToolResult.ok({"hp": 17})
    filtered = f.filter_result(
        tool_name="query_character",
        category=ToolCategory.READ,
        result=result,
        perspective_pc=None,
    )
    assert filtered.payload == {"hp": 17}


def test_noop_perception_filter_passes_through_not_found() -> None:
    f = NoopPerceptionFilter()
    result = ToolResult.not_found("no such monster")
    filtered = f.filter_result(
        tool_name="lookup_monster",
        category=ToolCategory.READ,
        result=result,
        perspective_pc="alex",
    )
    assert filtered.message == "no such monster"


def test_noop_perception_filter_passes_through_write_results() -> None:
    """Write tools' results carry mutation status; filter must not redact them."""
    f = NoopPerceptionFilter()
    result = ToolResult.ok({"applied": True, "new_hp": 5})
    filtered = f.filter_result(
        tool_name="apply_damage",
        category=ToolCategory.WRITE,
        result=result,
        perspective_pc="alex",
    )
    assert filtered.payload == {"applied": True, "new_hp": 5}


def test_noop_satisfies_protocol() -> None:
    f = NoopPerceptionFilter()
    assert isinstance(f, PerceptionFilter)
