"""Catalog primitives — registry types and global maps.

Submodules of :mod:`sidequest.telemetry.spans` import :class:`SpanRoute`
from here and mutate :data:`SPAN_ROUTES` / :data:`FLAT_ONLY_SPANS` in place
to register their domain's spans.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class _SpanLike(Protocol):
    """Structural stand-in for opentelemetry.sdk.trace.ReadableSpan."""

    name: str
    attributes: dict[str, Any] | None


@dataclass(frozen=True)
class SpanRoute:
    """Routing decision for a span family.

    When a routed span closes, the translator emits a typed WatcherEvent in
    addition to the always-on ``agent_span_close`` fan-out. The extractor
    reads from span attributes — they are the single source of truth for
    the typed event's ``fields``.
    """

    event_type: str
    component: str
    extract: Callable[[_SpanLike], dict[str, Any]]


# Spans intentionally without a typed-event route. Membership enforced by
# tests/telemetry/test_routing_completeness.py.
FLAT_ONLY_SPANS: set[str] = set()

# Span name -> SpanRoute. Each domain submodule registers its routed spans
# here near the constant declaration so renames break at import time and
# new constants without a routing decision trip the completeness lint.
SPAN_ROUTES: dict[str, SpanRoute] = {}
