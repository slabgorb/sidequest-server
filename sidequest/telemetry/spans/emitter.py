"""Emitter helper — fires OTEL events on the current span.

Span events differ from spans: they attach to whatever span is currently
active rather than starting a new one. Used for moments inside a turn
(dice request sent, throw received, result broadcast) where timing
relative to the parent turn matters more than carving out a child span.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace


class Emitter:
    """Fire OTEL events on the current span with typed attributes."""

    @staticmethod
    def fire(name: str, attrs: dict[str, Any]) -> None:
        """Add ``name`` as an event on the current span with ``attrs``."""
        trace.get_current_span().add_event(name, attributes=attrs)
