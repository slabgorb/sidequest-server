"""NarratorPerceptionFilter — concrete filter with per-tool rules.

Each per-tool rule is a function `(payload, perspective_pc) -> payload`.
Phase C tool conversions register their rule via the _RULES table.
Write tools are unfiltered (mutations are objective).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sidequest.agents.tool_registry import ToolCategory, ToolResult, ToolResultStatus

_RuleFn = Callable[[Any, str | None], Any]
_RULES: dict[str, _RuleFn] = {}


def register_rule(tool_name: str, fn: _RuleFn) -> None:
    if tool_name in _RULES:
        raise ValueError(f"Perception rule for {tool_name!r} already registered")
    _RULES[tool_name] = fn


class NarratorPerceptionFilter:
    def filter_result(
        self,
        *,
        tool_name: str,
        category: ToolCategory,
        result: ToolResult,
        perspective_pc: str | None,
    ) -> ToolResult:
        if category is ToolCategory.WRITE:
            return result
        if result.status is not ToolResultStatus.OK:
            return result
        rule = _RULES.get(tool_name)
        if rule is None:
            return result
        new_payload = rule(result.payload, perspective_pc)
        return ToolResult.ok(new_payload)
