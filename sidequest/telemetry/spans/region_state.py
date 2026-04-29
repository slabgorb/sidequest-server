"""Region-state spans — discovered_regions write-time rejection (Story 45-16).

``region.entry_rejected`` fires when the apply-time validator filters a
narrator-emitted region candidate that fails ``validate_region_name``
(empty / bracketed / multiline / too_long).

Sebastien's lie-detector audience needs to see *that* the filter fired
— ``rejection_count`` is the load-bearing audit field. ``reason`` and
``caller_path`` distinguish rejection types so the GM panel can show
which write seam caught the leak.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_REGION_ENTRY_REJECTED = "region.entry_rejected"


SPAN_ROUTES[SPAN_REGION_ENTRY_REJECTED] = SpanRoute(
    event_type="state_transition",
    component="region_state",
    extract=lambda span: {
        "field": "discovered_regions",
        "op": "entry_rejected",
        "entry": (span.attributes or {}).get("entry", ""),
        "entry_type": (span.attributes or {}).get("entry_type", ""),
        "reason": (span.attributes or {}).get("reason", ""),
        "caller_path": (span.attributes or {}).get("caller_path", ""),
        "rejection_count": (span.attributes or {}).get("rejection_count", 1),
    },
)


@contextmanager
def region_entry_rejected_span(
    *,
    entry: str,
    reason: str,
    caller_path: str,
    rejection_count: int = 1,
    entry_type: str = "string",
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Apply-time rejection of a non-room entry from ``discovered_regions``.

    ``entry`` is the rejected raw value (truncated by the caller if needed
    for log hygiene). ``reason`` is one of ``empty`` / ``bracketed`` /
    ``multiline`` / ``too_long``. ``caller_path`` identifies the write
    seam (e.g., ``narration_apply.location_update``,
    ``session.apply_patch.discover_regions``).
    """
    attributes: dict[str, Any] = {
        "entry": entry,
        "entry_type": entry_type,
        "reason": reason,
        "caller_path": caller_path,
        "rejection_count": rejection_count,
        **attrs,
    }
    with Span.open(
        SPAN_REGION_ENTRY_REJECTED, attributes, tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_REGION_ENTRY_REJECTED",
    "region_entry_rejected_span",
]
