"""Dungeon render spans — runtime cavern PNG sidecar emission (Story 52-4).

Closes the runtime-render OTEL gap (Epic 52). The materializer emits
``RegionMask`` (52-2), persistence stores it in ``dungeon_map.mask``
(52-3), and this story converts the persisted mask BLOB back into an
ADR-096 ``.cavern.png`` sidecar at room-enter time. Without an OTEL
span on the conversion the GM panel could not distinguish "PNG was
rendered" from "narrator improvised the existence of a map" — the
classic Illusionism failure mode CLAUDE.md's OTEL Observability
Principle exists to catch.

Attributes are sourced from the persisted mask dict (not the narrator),
so they ARE ground truth: ``region_id`` is the materializer's region id,
``mask_sha256`` is the SHA-256 of the persisted mask bytes (matches the
``mask_sha`` field in ``RegionMask.to_dict()``), ``grid_width`` /
``grid_height`` / ``cell_width`` come from the persisted ``block``
sub-dict.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_DUNGEON_RENDER_CAVERN_MASK_TO_PNG = "dungeon.render.cavern_mask_to_png"


def _attr(field: str):
    return lambda span, f=field: (span.attributes or {}).get(f)


SPAN_ROUTES[SPAN_DUNGEON_RENDER_CAVERN_MASK_TO_PNG] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_map.cavern_png",
        "op": "cavern_mask_to_png",
        "region_id": _attr("region_id")(s),
        "mask_sha256": _attr("mask_sha256")(s),
        "grid_width": _attr("grid_width")(s),
        "grid_height": _attr("grid_height")(s),
        "cell_width": _attr("cell_width")(s),
        "output_path": _attr("output_path")(s),
    },
)


@contextmanager
def cavern_mask_to_png_span(
    *,
    region_id: str,
    mask_sha256: str,
    grid_width: int,
    grid_height: int,
    cell_width: int,
    output_path: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Fires once per runtime cavern PNG sidecar emission.

    All mechanical params come from the persisted mask BLOB — the
    narrator cannot fabricate the SHA, dimensions, or output path.
    The span's presence is the GM panel's evidence that the runtime
    renderer engaged for a given ``region_id``; its absence proves the
    procedural cavern was not actually rendered (whatever the narrator
    may have claimed).
    """
    attributes: dict[str, Any] = {
        "region_id": region_id,
        "mask_sha256": mask_sha256,
        "grid_width": grid_width,
        "grid_height": grid_height,
        "cell_width": cell_width,
        "output_path": output_path,
        **attrs,
    }
    with Span.open(
        SPAN_DUNGEON_RENDER_CAVERN_MASK_TO_PNG,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span
