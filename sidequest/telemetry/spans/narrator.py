"""Narrator OTEL spans: sealed-round emission and session-rotation lifecycle (ADR-066 §10)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS
from .span import Span

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPAN_NARRATOR_SEALED_ROUND = "narrator.sealed_round"
SPAN_NARRATOR_SESSION_ROTATED = "narrator.session_rotated"
SPAN_NARRATOR_UNRECOVERABLE = "narrator.unrecoverable"

FLAT_ONLY_SPANS.update(
    {
        SPAN_NARRATOR_SEALED_ROUND,
        SPAN_NARRATOR_SESSION_ROTATED,
        SPAN_NARRATOR_UNRECOVERABLE,
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def narrator_session_rotated_span(
    *,
    reason: str,
    cumulative_tokens: int,
    turn_number: int,
    recap_chars: int,
    rebuild_latency_ms: int,
    threshold: int | None = None,
    cli_error_signature: str | None = None,
    _tracer: trace.Tracer | None = None,
) -> Iterator[trace.Span]:
    """Emit narrator.session_rotated; reason ∈ {cli_error, session_expired, token_threshold, unknown}."""
    attrs: dict[str, Any] = {
        "reason": reason,
        "cumulative_tokens": cumulative_tokens,
        "turn_number": turn_number,
        "recap_chars": recap_chars,
        "rebuild_latency_ms": rebuild_latency_ms,
    }
    if threshold is not None:
        attrs["threshold"] = threshold
    if cli_error_signature is not None:
        attrs["cli_error_signature"] = cli_error_signature
    with Span.open(SPAN_NARRATOR_SESSION_ROTATED, attrs, tracer_override=_tracer) as span:
        yield span


@contextlib.contextmanager
def narrator_unrecoverable_span(
    *,
    reason: str,
    first_error_signature: str,
    rebuild_error_signature: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
) -> Iterator[trace.Span]:
    """Emit narrator.unrecoverable when session rotation succeeds but the rebuild also fails (ADR-066 §8)."""
    attrs: dict[str, Any] = {
        "reason": reason,
        "first_error_signature": first_error_signature,
        "rebuild_error_signature": rebuild_error_signature,
        "turn_number": turn_number,
    }
    with Span.open(SPAN_NARRATOR_UNRECOVERABLE, attrs, tracer_override=_tracer) as span:
        span.set_status(trace.Status(trace.StatusCode.ERROR, "narrator unrecoverable"))
        yield span
