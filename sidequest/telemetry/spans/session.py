"""Hub + delve lifecycle spans (Sünden engine plan Task 12).

Five watcher event-types fired by the hub→delve→hub cycle. Together they
are the GM panel's lie detector for the delve engine: when the narrator
says "you enter the Grimvault", the corresponding ``session.delve_started``
event MUST appear in the watcher stream — otherwise the engine never
engaged and Claude is improvising.

These are watcher event-types, not OTEL spans started via ``Span.open``.
``_watcher_publish`` already mints a synthetic OTLP span when
``SIDEQUEST_WATCHER_AS_SPANS=1``, so the bridge to Jaeger is automatic.
The constants live here purely so callsites can import a stable name
rather than repeating the string. They are added to ``FLAT_ONLY_SPANS``
to satisfy the routing-completeness lint without inventing a passthrough
``SpanRoute`` extractor — the dashboard reads ``event.fields`` directly
for these types.
"""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_SESSION_HUB_MODE_ENTERED = "session.hub_mode_entered"
SPAN_SESSION_DELVE_STARTED = "session.delve_started"
SPAN_SESSION_DELVE_ENDED = "session.delve_ended"
SPAN_SESSION_HIRELING_RECRUITED = "session.hireling_recruited"
SPAN_SESSION_HIRELING_DISMISSED = "session.hireling_dismissed"

FLAT_ONLY_SPANS.update(
    {
        SPAN_SESSION_HUB_MODE_ENTERED,
        SPAN_SESSION_DELVE_STARTED,
        SPAN_SESSION_DELVE_ENDED,
        SPAN_SESSION_HIRELING_RECRUITED,
        SPAN_SESSION_HIRELING_DISMISSED,
    }
)
