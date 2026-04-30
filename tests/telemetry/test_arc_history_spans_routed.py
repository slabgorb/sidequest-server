"""Story 45-19 — the new world_history spans must be routed.

Per ADR-031 / context-story-45-19.md, ``world_history.arc_tick`` and
``world_history.arc_promoted`` are state-transition events the GM
panel surfaces on its typed Subsystems tab. Without an entry in
``SPAN_ROUTES`` the watcher would emit only the always-on
``agent_span_close`` and the typed tab would silently miss the
arc-tick subsystem — exactly the failure mode Sebastien's lie detector
is supposed to catch.

These tests pin the routing decision in place so a future rename or
reshape of the helper trips a hard test failure rather than a silent
dashboard gap.
"""

from __future__ import annotations

from sidequest.telemetry.spans import (
    SPAN_ROUTES,
    SPAN_WORLD_HISTORY_ARC_PROMOTED,
    SPAN_WORLD_HISTORY_ARC_TICK,
)


def test_arc_tick_span_constant_value() -> None:
    """The constant must match the canonical span name used by the GM
    panel. Renaming the constant without renaming the canonical name
    would silently break the watcher's filter rules.
    """

    assert SPAN_WORLD_HISTORY_ARC_TICK == "world_history.arc_tick"


def test_arc_promoted_span_constant_value() -> None:
    assert SPAN_WORLD_HISTORY_ARC_PROMOTED == "world_history.arc_promoted"


def test_arc_tick_is_routed_as_state_transition() -> None:
    """``arc_tick`` carries the per-tick payload (interaction, round,
    maturity, chapter counts). It belongs on the state_transition
    typed event so the dashboard's Subsystems tab can chart it.
    """

    assert SPAN_WORLD_HISTORY_ARC_TICK in SPAN_ROUTES, (
        "world_history.arc_tick must be registered in SPAN_ROUTES; "
        "FLAT_ONLY_SPANS would route it only to agent_span_close and "
        "the GM panel's typed Subsystems tab would not see arc ticks."
    )
    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_TICK]
    assert route.event_type == "state_transition"
    assert route.component, "arc_tick route must declare a component name"


def test_arc_promoted_is_routed_as_state_transition() -> None:
    assert SPAN_WORLD_HISTORY_ARC_PROMOTED in SPAN_ROUTES, (
        "world_history.arc_promoted must be registered in SPAN_ROUTES."
    )
    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_PROMOTED]
    assert route.event_type == "state_transition"
    assert route.component, (
        "arc_promoted route must declare a component name"
    )


def test_arc_tick_extract_pulls_required_attributes() -> None:
    """The route's ``extract`` callable must surface the load-bearing
    attributes (interaction, round, maturity, chapter counts,
    tier_changed) so the watcher event payload carries what the GM
    panel needs without re-reading the raw span.
    """

    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_TICK]

    class _FakeSpan:
        name = "world_history.arc_tick"
        attributes = {
            "interaction": 5,
            "round": 5,
            "from_maturity": "Fresh",
            "to_maturity": "Early",
            "chapters_before": 0,
            "chapters_after": 1,
            "tier_changed": True,
            "cadence_interval": 5,
        }

    fields = route.extract(_FakeSpan())
    for required in (
        "interaction",
        "round",
        "from_maturity",
        "to_maturity",
        "chapters_before",
        "chapters_after",
        "tier_changed",
    ):
        assert required in fields, (
            f"arc_tick route extract() missing {required!r}; got {sorted(fields)}"
        )


def test_arc_promoted_extract_pulls_transition_attributes() -> None:
    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_PROMOTED]

    class _FakeSpan:
        name = "world_history.arc_promoted"
        attributes = {
            "interaction": 21,
            "from_maturity": "Early",
            "to_maturity": "Mid",
            "chapters_added": ["mid"],
        }

    fields = route.extract(_FakeSpan())
    for required in ("interaction", "from_maturity", "to_maturity", "chapters_added"):
        assert required in fields, (
            f"arc_promoted route extract() missing {required!r}; got {sorted(fields)}"
        )
