"""OTEL span for cavern room loading. ADR-096.

Emitted whenever the room loader produces a cavern payload. The GM panel
uses these to verify the right map loaded — Claude can't fake cellular
params or floor counts since they come from the loader, not the
narrator.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_CAVERN_ROOM_LOAD = "cavern_room.load"

# ---------------------------------------------------------------------------
# SPAN_ROUTES — surface cavern loads as a ``cavern_room_load`` event under
# ``component=cavern_room`` so the GM panel renders them on their own lane.
# ---------------------------------------------------------------------------

SPAN_ROUTES[SPAN_CAVERN_ROOM_LOAD] = SpanRoute(
    event_type="state_transition",
    component="cavern_room",
    extract=lambda span: {
        "field": "cavern_room",
        "op": "cavern_room_load",
        "room_id": (span.attributes or {}).get("room_id", ""),
        "seed": (span.attributes or {}).get("seed", 0),
        "density": (span.attributes or {}).get("density", 0.0),
        "floor_count": (span.attributes or {}).get("floor_count", 0),
        "mask_sha256": (span.attributes or {}).get("mask_sha256", ""),
        "cavern_image_url": (span.attributes or {}).get("cavern_image_url", ""),
    },
)


@contextmanager
def cavern_room_load_span(
    *,
    room_id: str,
    seed: int,
    density: float,
    floor_count: int,
    mask: str,
    cavern_image_url: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Fires once per cavern room load. All mechanical params come from
    the loader — seed, density, mask hash, and floor count are ground
    truth the narrator cannot fabricate."""
    attributes: dict[str, Any] = {
        "room_id": room_id,
        "seed": seed,
        "density": density,
        "floor_count": floor_count,
        "mask_sha256": hashlib.sha256(mask.encode()).hexdigest()[:16],
        "cavern_image_url": cavern_image_url,
        **attrs,
    }
    with Span.open(
        SPAN_CAVERN_ROOM_LOAD,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span
