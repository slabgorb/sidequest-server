"""PerceptionFilter — Phase B primitive.

The narrator's tool path runs every tool result through a PerceptionFilter
before handing it back to the model. The Noop default passes everything
through unchanged; Phase C wires per-tool filter rules (see spec §Perception
filtering at the tool layer for the per-tool rule table).

Successor to ADR-028's post-pass rewriter approach. The legacy
PerceptionRewriter remains live until Phase D wires per-tool filter rules
into production and retires it.

Write tools' results are intentionally not redacted: mutation status must
be objectively reported. The filter inspects category to decide whether
redaction is in scope.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sidequest.agents.tool_registry import ToolCategory, ToolResult


@runtime_checkable
class PerceptionFilter(Protocol):
    def filter_result(
        self,
        *,
        tool_name: str,
        category: ToolCategory,
        result: ToolResult,
        perspective_pc: str | None,
    ) -> ToolResult: ...


class NoopPerceptionFilter:
    """Default — pass through. Use in tests + Phase B integration."""

    def filter_result(
        self,
        *,
        tool_name: str,
        category: ToolCategory,
        result: ToolResult,
        perspective_pc: str | None,
    ) -> ToolResult:
        return result
