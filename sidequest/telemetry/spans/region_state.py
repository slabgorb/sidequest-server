"""Region-state spans — discovered_regions write-time guards
(Stories 45-16, 45-17).

``region.entry_rejected`` (45-16) fires when the apply-time validator
filters a narrator-emitted region candidate that fails
``validate_region_name`` (empty / bracketed / multiline / too_long).

``region.entry_canonicalized_dedup`` (45-17) fires when a candidate
*passes* validation but its canonical slug already exists in
``discovered_regions`` — the write would have produced a surface-variant
duplicate (Felix's ``"The Crew Quarters"`` / ``"the crew quarters"``
playtest leak). Audit attributes carry both the raw incoming entry
and the existing surface form so the GM panel can show *which* form
won.

Sebastien's lie-detector audience needs to see *that* the guard fired —
``rejection_count`` / ``dedup_count`` are the load-bearing audit
fields. ``reason`` / ``caller_path`` distinguish guard types so the
GM panel can show which write seam caught the leak.
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
        SPAN_REGION_ENTRY_REJECTED,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span


SPAN_REGION_ENTRY_CANONICALIZED_DEDUP = "region.entry_canonicalized_dedup"


SPAN_ROUTES[SPAN_REGION_ENTRY_CANONICALIZED_DEDUP] = SpanRoute(
    event_type="state_transition",
    component="region_state",
    extract=lambda span: {
        "field": "discovered_regions",
        "op": "canonicalized_dedup",
        "entry": (span.attributes or {}).get("entry", ""),
        "canonical_slug": (span.attributes or {}).get("canonical_slug", ""),
        "existing_surface_form": (span.attributes or {}).get(
            "existing_surface_form",
            "",
        ),
        "caller_path": (span.attributes or {}).get("caller_path", ""),
        "dedup_count": (span.attributes or {}).get("dedup_count", 1),
    },
)


@contextmanager
def region_entry_canonicalized_dedup_span(
    *,
    entry: str,
    canonical_slug: str,
    existing_surface_form: str,
    caller_path: str,
    dedup_count: int = 1,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Apply-time canonical-dedup of a surface-variant entry.

    ``entry`` is the incoming raw value the narrator emitted.
    ``canonical_slug`` is its slug per
    ``canonicalize_region_name``. ``existing_surface_form`` is the
    already-stored entry whose slug collides — the GM panel can show
    *both* surface forms so the dashboard reads "narrator said X but Y
    is already canon for that room". ``caller_path`` identifies the
    write seam.
    """
    attributes: dict[str, Any] = {
        "entry": entry,
        "canonical_slug": canonical_slug,
        "existing_surface_form": existing_surface_form,
        "caller_path": caller_path,
        "dedup_count": dedup_count,
        **attrs,
    }
    with Span.open(
        SPAN_REGION_ENTRY_CANONICALIZED_DEDUP,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_REGION_ENTRY_CANONICALIZED_DEDUP",
    "SPAN_REGION_ENTRY_REJECTED",
    "region_entry_canonicalized_dedup_span",
    "region_entry_rejected_span",
]
