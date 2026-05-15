"""Tests for NarratorPerceptionFilter — dispatches per-tool rules."""

from __future__ import annotations

from sidequest.agents.narrator_perception_filter import NarratorPerceptionFilter
from sidequest.agents.perception_filter import PerceptionFilter
from sidequest.agents.tool_registry import ToolCategory, ToolResult


def test_filter_conforms_to_protocol() -> None:
    assert isinstance(NarratorPerceptionFilter(), PerceptionFilter)


def test_filter_passes_through_unknown_tool() -> None:
    f = NarratorPerceptionFilter()
    r = ToolResult.ok({"x": 1})
    out = f.filter_result(
        tool_name="brand_new_tool",
        category=ToolCategory.READ,
        result=r,
        perspective_pc="alex",
    )
    assert out.payload == {"x": 1}


def test_filter_passes_through_write_results() -> None:
    f = NarratorPerceptionFilter()
    r = ToolResult.ok({"applied": True})
    out = f.filter_result(
        tool_name="apply_damage",
        category=ToolCategory.WRITE,
        result=r,
        perspective_pc="alex",
    )
    assert out.payload == {"applied": True}
