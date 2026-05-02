"""Magic spans — narrator-described workings (Coyote Star iter 3, Task 3.5).

Routes to the dashboard event feed via SPAN_ROUTES with
``event_type=state_transition``, ``component=magic``. The OTEL span
carries the structured attributes; the WatcherSpanProcessor re-emits
them through the route's ``extract`` lambda so the GM panel sees the
working as part of its existing event feed without needing a new UI tab.

Span attributes are JSON-encoded for the structured payloads (``costs``,
``flags``, ``ledger_after``) because OTEL silently drops dict/list
attribute values; the route extractor decodes them back at the boundary.
This mirrors the ``audio.skipped`` / ``lore.established`` precedent.
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from sidequest.magic.models import Flag

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_MAGIC_WORKING = "magic.working"

SPAN_ROUTES[SPAN_MAGIC_WORKING] = SpanRoute(
    event_type="state_transition",
    component="magic",
    extract=lambda span: {
        "field": "magic_state",
        "op": "working",
        "plugin": (span.attributes or {}).get("plugin", ""),
        "actor": (span.attributes or {}).get("actor", ""),
        "mechanism_engaged": (span.attributes or {}).get("mechanism_engaged", ""),
        "domain": (span.attributes or {}).get("domain", ""),
        "narrator_basis": (span.attributes or {}).get("narrator_basis", ""),
        "costs_debited": _json.loads((span.attributes or {}).get("costs_debited_json", "{}")),
        "flags": _json.loads((span.attributes or {}).get("flags_json", "[]")),
        "ledger_after": _json.loads((span.attributes or {}).get("ledger_after_json", "{}")),
        "flavor": (span.attributes or {}).get("flavor", ""),
        "consent_state": (span.attributes or {}).get("consent_state", ""),
        "item_id": (span.attributes or {}).get("item_id", ""),
        "alignment_with_item_nature": (span.attributes or {}).get(
            "alignment_with_item_nature", 0.0
        ),
    },
)


@contextmanager
def magic_working_span(
    *,
    plugin: str,
    mechanism: str,
    actor: str,
    domain: str,
    narrator_basis: str,
    costs_debited: dict[str, float],
    flags: list[Flag],
    ledger_after: dict[str, float],
    flavor: str | None = None,
    consent_state: str | None = None,
    item_id: str | None = None,
    alignment_with_item_nature: float | None = None,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the ``magic.working`` OTEL span.

    Plugin-specific extras (``flavor`` / ``consent_state`` for innate_v1,
    ``item_id`` / ``alignment_with_item_nature`` for item_legacy_v1) are
    optional — None values coerce to the OTEL-safe defaults the route
    extractor falls back to. ``flags`` is serialised via ``model_dump``
    so the route can rehydrate the validator's ``Flag`` records on the
    other side.
    """
    attributes: dict[str, Any] = {
        "plugin": plugin,
        "mechanism_engaged": mechanism,
        "actor": actor,
        "domain": domain,
        "narrator_basis": narrator_basis,
        "costs_debited_json": _json.dumps(dict(costs_debited), sort_keys=True),
        "flags_json": _json.dumps([f.model_dump() for f in flags]),
        "ledger_after_json": _json.dumps(dict(ledger_after), sort_keys=True),
        "flavor": flavor or "",
        "consent_state": consent_state or "",
        "item_id": item_id or "",
        "alignment_with_item_nature": (
            float(alignment_with_item_nature) if alignment_with_item_nature is not None else 0.0
        ),
        **attrs,
    }
    with Span.open(SPAN_MAGIC_WORKING, attributes, tracer_override=_tracer) as span:
        yield span
