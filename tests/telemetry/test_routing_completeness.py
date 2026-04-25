"""Static lint: every SPAN_* constant must have a routing decision.

A new span constant added to spans.py without either an entry in
SPAN_ROUTES or membership in FLAT_ONLY_SPANS is a routing gap — the
translator will emit only agent_span_close, and the dashboard's typed
tabs will silently miss the new subsystem. This test forces the
decision to be explicit at the point a constant is introduced.
"""

from __future__ import annotations

from sidequest.telemetry import spans
from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES


def _all_span_constants() -> set[str]:
    """Every SPAN_* attribute on the spans module that holds a string."""
    return {
        v
        for name, v in vars(spans).items()
        if name.startswith("SPAN_") and isinstance(v, str)
    }


def test_every_span_is_routed_or_explicitly_flat() -> None:
    all_spans = _all_span_constants()
    routed = set(SPAN_ROUTES.keys())
    flat = set(FLAT_ONLY_SPANS)
    missing = all_spans - routed - flat
    overlap = routed & flat

    assert not overlap, (
        f"Spans cannot be both routed AND flat-only: {sorted(overlap)}"
    )
    assert not missing, (
        "Spans without a routing decision (add to SPAN_ROUTES or "
        f"FLAT_ONLY_SPANS): {sorted(missing)}"
    )


def test_routes_target_known_event_types() -> None:
    """Each SpanRoute.event_type matches a WatcherEventType the dashboard
    handles. This is a string check — the source of truth is
    sidequest-ui/src/types/watcher.ts."""
    known = {
        "agent_span_open",
        "agent_span_close",
        "state_transition",
        "turn_complete",
        "lore_retrieval",
        "prompt_assembled",
        "game_state_snapshot",
        "validation_warning",
        "subsystem_exercise_summary",
        "coverage_gap",
        "json_extraction_result",
    }
    bad = [
        (name, route.event_type)
        for name, route in SPAN_ROUTES.items()
        if route.event_type not in known
    ]
    assert not bad, f"Routes targeting unknown event types: {bad}"
