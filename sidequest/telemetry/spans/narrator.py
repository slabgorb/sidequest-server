"""Narrator spans — sealed-round emission and session lifecycle events.

ADR-066 §10 (2026-05-04 amendment): every narrator session rotation
(proactive watchdog or reactive recovery) emits ``narrator.session_rotated``
so the GM panel can verify the fix is engaged. If recovery itself fails,
``narrator.unrecoverable`` fires as the last-resort signal before the
player sees an in-fiction stall.
"""

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
    """Emit ``narrator.session_rotated`` (ADR-066 §10).

    ``reason`` is one of:
      * ``cli_error`` — context-overflow or other CLI failure (reactive)
      * ``session_expired`` — CLI says session not found / expired (reactive)
      * ``token_threshold`` — proactive watchdog (story 45-48)
      * ``unknown`` — catch-all for unexpected errors

    Optional fields are only set on applicable reasons:
      * ``threshold`` — only on proactive (``token_threshold``)
      * ``cli_error_signature`` — only on reactive failures
    """
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
    """Emit ``narrator.unrecoverable`` when recovery itself fails (ADR-066 §8).

    The companion event to ``narrator.session_rotated``: a rotation fired
    but the rebuild turn also failed. The orchestrator returns a graceful
    in-fiction stall to the player; this span lets the GM panel see the
    double-failure without log diving.
    """
    attrs: dict[str, Any] = {
        "reason": reason,
        "first_error_signature": first_error_signature,
        "rebuild_error_signature": rebuild_error_signature,
        "turn_number": turn_number,
    }
    with Span.open(SPAN_NARRATOR_UNRECOVERABLE, attrs, tracer_override=_tracer) as span:
        span.set_status(trace.Status(trace.StatusCode.ERROR, "narrator unrecoverable"))
        yield span
