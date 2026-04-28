"""Span-route static checks for ``turn_manager.round_invariant``.

Story 45-11 AC3: the invariant span must route through ``SPAN_ROUTES`` to
the GM panel watcher feed as a typed ``state_transition`` event with
``component="turn_manager"`` and a ``field="round_invariant"`` payload.
Without the route registration the span emits only the firehose
``agent_span_close`` event and the dashboard's typed Subsystems tab stays
dark — exactly the "telemetry that fires but says nothing" pattern Epic 45
is closing.

Static routing checks live here at unit-test speed — no watcher hub,
no test session. The full runtime end-to-end check (span emit →
WatcherSpanProcessor → WatcherHub → subscriber) lives in
``tests/server/test_turn_manager_round_invariant.py`` because it needs
the ``session_handler_factory`` fixture defined in ``tests/server/conftest.py``.

These tests are RED until 45-11's GREEN phase registers the span.
"""

from __future__ import annotations

from sidequest.telemetry.spans import SPAN_ROUTES

SPAN_NAME = "turn_manager.round_invariant"


# ---------------------------------------------------------------------------
# Static routing — cheap, complements the runtime test below
# ---------------------------------------------------------------------------


def test_round_invariant_span_has_route_registered() -> None:
    """A SPAN_ROUTES entry must exist with the documented event_type and
    component. Without it the watcher's on_end will fall back to flat-only
    emission and the typed Subsystems tab will not light up."""
    assert SPAN_NAME in SPAN_ROUTES, (
        f"{SPAN_NAME!r} not registered in SPAN_ROUTES — the watcher will "
        f"emit only agent_span_close, the dashboard's typed view stays dark"
    )
    route = SPAN_ROUTES[SPAN_NAME]
    assert route.event_type == "state_transition", (
        f"event_type={route.event_type!r}, expected 'state_transition' "
        f"(per context-story-45-11.md OTEL spans table)"
    )
    assert route.component == "turn_manager", (
        f"component={route.component!r}, expected 'turn_manager'"
    )


def test_round_invariant_route_extracts_required_fields() -> None:
    """The route's ``extract`` callable must lift round/interaction/
    max_narrative_round/gap/holds from the span attributes — without these
    fields the GM panel chart has nothing to render."""
    route = SPAN_ROUTES[SPAN_NAME]

    class _FakeSpan:
        name = SPAN_NAME
        attributes = {
            "round": 72,
            "interaction": 72,
            "max_narrative_round": 72,
            "gap": 0,
            "holds": True,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]

    # The extractor MUST surface the values that drive the dashboard's
    # invariant-violation colouring.
    assert fields.get("field") == "round_invariant", (
        f"extract() must set field='round_invariant'; got {fields.get('field')!r}"
    )
    assert fields.get("round") == 72
    assert fields.get("interaction") == 72
    assert fields.get("max_narrative_round") == 72
    assert fields.get("gap") == 0
    assert fields.get("holds") is True


def test_round_invariant_route_extracts_violation_fields() -> None:
    """A divergent (Felix-style) span must lift gap>0 and holds=False so the
    dashboard can colour the violation row red."""
    route = SPAN_ROUTES[SPAN_NAME]

    class _FakeSpan:
        name = SPAN_NAME
        attributes = {
            "round": 65,
            "interaction": 72,
            "max_narrative_round": 72,
            "gap": 7,
            "holds": False,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]
    assert fields.get("gap") == 7
    assert fields.get("holds") is False
    assert fields.get("round") == 65
    assert fields.get("max_narrative_round") == 72


# Runtime end-to-end (span emit → WatcherSpanProcessor → hub subscriber)
# is exercised by tests/server/test_turn_manager_round_invariant.py.
