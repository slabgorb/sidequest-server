"""ADR-107 — routed aside.resolve span (RED, story 50-25).

Plan: docs/superpowers/plans/2026-05-17-aside-channel.md Task 2.
Fails until Dev creates sidequest/telemetry/spans/aside.py and registers
it in spans/__init__.py (GREEN). The span is ROUTED (not flat-only)
because an ungrounded aside is exactly the narrator-lie the GM panel
must catch (CLAUDE.md OTEL Observability Principle).
"""

from sidequest.telemetry.spans import SPAN_ROUTES
from sidequest.telemetry.spans.aside import SPAN_ASIDE_RESOLVE


def test_aside_resolve_is_routed():
    assert SPAN_ASIDE_RESOLVE == "aside.resolve"
    assert SPAN_ASIDE_RESOLVE in SPAN_ROUTES
    route = SPAN_ROUTES[SPAN_ASIDE_RESOLVE]
    assert route.event_type == "state_transition"
    assert route.component == "aside"


def test_aside_resolve_extract_pulls_attributes():
    route = SPAN_ROUTES[SPAN_ASIDE_RESOLVE]

    class _Span:
        name = "aside.resolve"
        attributes = {
            "asker_id": "Hiken",
            "outcome": "answered",
            "grounded_on": "character.size,region.water_depth",
            "model": "haiku",
            "latency_ms": 412,
        }

    fields = route.extract(_Span())
    assert fields["asker_id"] == "Hiken"
    assert fields["outcome"] == "answered"
    assert fields["op"] == "resolved"
