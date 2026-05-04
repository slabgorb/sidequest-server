"""Story 45-27 — span-route static checks for the trope-tempo telemetry.

The story moves the trope-engine spans out of ``FLAT_ONLY_SPANS`` and
into ``SPAN_ROUTES`` so the GM panel's typed Subsystems feed surfaces
trope tempo as ``state_transition`` events alongside the other Lane B
write-back spans (45-9 ``total_beats_fired``, 45-19 arc-history,
45-20 trope-resolution handshake).

Without these route entries the watcher's ``on_end`` hook falls back to
firehose-only emission and the dashboard's typed view stays dark — the
"telemetry that fires but says nothing" pattern Epic 45 is closing.

Static checks live here at unit-test speed; the runtime span-emit →
WatcherSpanProcessor → subscriber wire-up is exercised by
``tests/server/test_45_27_trope_tempo_wire.py`` against the
``session_handler_factory`` fixture.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# turn.tropes — the per-turn aggregate. THE GM panel's tempo chart.
# ---------------------------------------------------------------------------


SPAN_TURN_TROPES = "turn.tropes"


def test_turn_tropes_is_routed_not_flat_only() -> None:
    """``turn.tropes`` MUST be in ``SPAN_ROUTES``. Pre-45-27 it lived in
    ``FLAT_ONLY_SPANS`` (a Phase 2 baseline that the port carried through
    unchanged); the story moves it into typed routing so the panel can
    chart tempo per turn.
    """

    from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES

    assert SPAN_TURN_TROPES in SPAN_ROUTES, (
        f"{SPAN_TURN_TROPES!r} not registered in SPAN_ROUTES — the "
        f"watcher will emit only agent_span_close and the GM panel's "
        f"tempo chart will stay dark. Without typed routing the per-"
        f"turn metrics are unreachable to subscribers."
    )
    assert SPAN_TURN_TROPES not in FLAT_ONLY_SPANS, (
        f"{SPAN_TURN_TROPES!r} still in FLAT_ONLY_SPANS — duplicate "
        f"registration produces inconsistent watcher behavior. "
        f"Remove it from FLAT_ONLY_SPANS when adding the route."
    )


def test_turn_tropes_route_dispatches_to_tropes_component() -> None:
    """Component='tropes' so the GM panel filters all five trope spans
    (turn.tropes, trope.tick, trope_activate, trope.cap_blocked,
    trope.cooldown_blocked) through one Subsystems-tab predicate.
    """

    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_TURN_TROPES]
    assert route.event_type == "state_transition", (
        f"event_type={route.event_type!r}, expected 'state_transition' "
        "to match the typed Subsystems feed contract."
    )
    assert route.component == "tropes", (
        f"component={route.component!r}, expected 'tropes' so the panel "
        "filter groups the five trope spans together."
    )


def test_turn_tropes_route_extracts_three_required_metrics() -> None:
    """The story description names three metrics the GM panel needs:
    ``active_trope_count`` / ``progression_max`` / ``progression_avg``.
    The extractor must surface all three or the chart cannot render.
    """

    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_TURN_TROPES]

    class _FakeSpan:
        name = SPAN_TURN_TROPES
        attributes = {
            "active_trope_count": 2,
            "progression_max": 0.65,
            "progression_avg": 0.42,
            "queued_count": 1,
            "cooldown_active": False,
            "turn_number": 17,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]

    # Story-required metrics — these are the GM panel's chart inputs.
    assert fields.get("active_trope_count") == 2
    assert fields.get("progression_max") == pytest.approx(0.65)
    assert fields.get("progression_avg") == pytest.approx(0.42)


def test_turn_tropes_route_extracts_diagnostic_metrics() -> None:
    """Beyond the chart inputs, the route surfaces ``queued_count`` and
    ``cooldown_active`` so a tempo dip is *explainable* on the panel —
    "did the cap engage" / "is cooldown holding back activations" — not
    just visible.
    """

    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_TURN_TROPES]

    class _FakeSpan:
        name = SPAN_TURN_TROPES
        attributes = {
            "active_trope_count": 3,
            "progression_max": 0.30,
            "progression_avg": 0.15,
            "queued_count": 1,
            "cooldown_active": True,
            "turn_number": 18,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]
    assert fields.get("queued_count") == 1
    assert fields.get("cooldown_active") is True


def test_turn_tropes_route_field_is_active_tropes() -> None:
    """``field='active_tropes'`` namespaces the typed event to the
    snapshot field the panel renders. Mirrors the 45-20 handshake's
    ``field='quest_log'`` discipline.
    """

    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_TURN_TROPES]

    class _FakeSpan:
        name = SPAN_TURN_TROPES
        attributes = {"active_trope_count": 0, "progression_max": 0.0, "progression_avg": 0.0}

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]
    assert fields.get("field") == "active_tropes", (
        f"field={fields.get('field')!r}, expected 'active_tropes' for panel-side filtering."
    )


# ---------------------------------------------------------------------------
# trope.tick — per-trope tick. Charts which tropes moved this turn.
# ---------------------------------------------------------------------------


SPAN_TROPE_TICK_PER = "trope.tick"


def test_trope_tick_per_is_routed() -> None:
    """Per-trope tick span — the panel renders a per-trope progression
    sparkline alongside the aggregate. Without the route the panel
    sees only the aggregate average and cannot diagnose which trope
    is moving.
    """

    from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES

    assert SPAN_TROPE_TICK_PER in SPAN_ROUTES, (
        f"{SPAN_TROPE_TICK_PER!r} not in SPAN_ROUTES; panel cannot "
        "render per-trope progression sparklines."
    )
    assert SPAN_TROPE_TICK_PER not in FLAT_ONLY_SPANS


def test_trope_tick_per_route_extracts_progress_delta() -> None:
    """The per-trope span carries before/after progress so the panel
    can highlight movement (and stillness — a trope stuck at the same
    progress for many turns is a symptom of accelerator/decelerator
    misconfiguration).
    """

    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_TROPE_TICK_PER]
    assert route.event_type == "state_transition"
    assert route.component == "tropes"

    class _FakeSpan:
        name = SPAN_TROPE_TICK_PER
        attributes = {
            "trope_id": "the_keeper_stirs",
            "progress_before": 0.10,
            "progress_after": 0.15,
            "delta": 0.05,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]
    assert fields.get("trope_id") == "the_keeper_stirs"
    assert fields.get("progress_before") == pytest.approx(0.10)
    assert fields.get("progress_after") == pytest.approx(0.15)
    assert fields.get("delta") == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# trope_activate — dormant→progressing transition.
# ---------------------------------------------------------------------------


SPAN_TROPE_ACTIVATE = "trope_activate"


def test_trope_activate_is_routed() -> None:
    from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES

    assert SPAN_TROPE_ACTIVATE in SPAN_ROUTES, (
        f"{SPAN_TROPE_ACTIVATE!r} not in SPAN_ROUTES; panel cannot "
        "render the per-trope lifecycle timeline (dormant→progressing→"
        "resolved)."
    )
    assert SPAN_TROPE_ACTIVATE not in FLAT_ONLY_SPANS


def test_trope_activate_route_carries_cap_used() -> None:
    """``cap_used`` lets the panel show "3 of 3 slots used" alongside
    the activation. Sebastien-tier mechanical visibility — the cap
    isn't just a number, it's a state.
    """

    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_TROPE_ACTIVATE]
    assert route.component == "tropes"

    class _FakeSpan:
        name = SPAN_TROPE_ACTIVATE
        attributes = {
            "trope_id": "extraction_panic",
            "from_status": "dormant",
            "to_status": "progressing",
            "cap_used": 2,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]
    assert fields.get("trope_id") == "extraction_panic"
    assert fields.get("cap_used") == 2


# ---------------------------------------------------------------------------
# trope.cap_blocked — diagnostic when the cap holds a candidate back.
# ---------------------------------------------------------------------------


SPAN_TROPE_CAP_BLOCKED = "trope.cap_blocked"


def test_trope_cap_blocked_constant_exists() -> None:
    """The constant must be importable from the trope spans module so
    the engine can reference it without re-typing the literal — per
    ADR-068 magic-literal extraction.
    """

    from sidequest.telemetry.spans import SPAN_TROPE_CAP_BLOCKED as CONST

    assert CONST == SPAN_TROPE_CAP_BLOCKED


def test_trope_cap_blocked_is_routed() -> None:
    from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES

    assert SPAN_TROPE_CAP_BLOCKED in SPAN_ROUTES, (
        f"{SPAN_TROPE_CAP_BLOCKED!r} not in SPAN_ROUTES; the panel "
        "cannot distinguish 'cap engaged' from 'engine never ran'."
    )
    assert SPAN_TROPE_CAP_BLOCKED not in FLAT_ONLY_SPANS


def test_trope_cap_blocked_route_extracts_diagnostic_fields() -> None:
    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_TROPE_CAP_BLOCKED]
    assert route.event_type == "state_transition"
    assert route.component == "tropes"

    class _FakeSpan:
        name = SPAN_TROPE_CAP_BLOCKED
        attributes = {
            "trope_id": "the_deeper_dark",
            "current_active_count": 3,
            "cap": 3,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]
    assert fields.get("trope_id") == "the_deeper_dark"
    assert fields.get("current_active_count") == 3
    assert fields.get("cap") == 3


# ---------------------------------------------------------------------------
# trope.cooldown_blocked — diagnostic when cooldown holds a candidate back.
# ---------------------------------------------------------------------------


SPAN_TROPE_COOLDOWN_BLOCKED = "trope.cooldown_blocked"


def test_trope_cooldown_blocked_constant_exists() -> None:
    from sidequest.telemetry.spans import SPAN_TROPE_COOLDOWN_BLOCKED as CONST

    assert CONST == SPAN_TROPE_COOLDOWN_BLOCKED


def test_trope_cooldown_blocked_is_routed() -> None:
    from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES

    assert SPAN_TROPE_COOLDOWN_BLOCKED in SPAN_ROUTES, (
        f"{SPAN_TROPE_COOLDOWN_BLOCKED!r} not in SPAN_ROUTES; the panel "
        "cannot show cooldown engagement."
    )
    assert SPAN_TROPE_COOLDOWN_BLOCKED not in FLAT_ONLY_SPANS


def test_trope_cooldown_blocked_route_carries_until_turn() -> None:
    """``cooldown_until_turn`` lets the panel show "cooldown for N more
    turns" rather than a static "blocked" indicator.
    """

    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_TROPE_COOLDOWN_BLOCKED]

    class _FakeSpan:
        name = SPAN_TROPE_COOLDOWN_BLOCKED
        attributes = {
            "trope_id": "extraction_panic",
            "cooldown_until_turn": 14,
            "current_turn": 12,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]
    assert fields.get("trope_id") == "extraction_panic"
    assert fields.get("cooldown_until_turn") == 14
    assert fields.get("current_turn") == 12


# ---------------------------------------------------------------------------
# trope_resolve — the existing route gains a new attribute.
# ---------------------------------------------------------------------------


SPAN_TROPE_RESOLVE = "trope_resolve"


def test_trope_resolve_route_carries_cooldown_until_turn() -> None:
    """Story AC6: the resolution span must carry
    ``cooldown_until_turn`` so the GM panel can show the cooldown bar
    starting at resolution. Pre-45-27 the route exists (45-20) but
    does not surface this attribute.
    """

    from sidequest.telemetry.spans import SPAN_ROUTES

    assert SPAN_TROPE_RESOLVE in SPAN_ROUTES
    route = SPAN_ROUTES[SPAN_TROPE_RESOLVE]

    class _FakeSpan:
        name = SPAN_TROPE_RESOLVE
        attributes = {
            "trope_id": "extraction_panic",
            "interaction": 18,
            "genre_slug": "caverns_and_claudes",
            "final_progress": 1.0,
            "beats_fired_total": 4,
            "cooldown_until_turn": 20,
        }

    fields = route.extract(_FakeSpan())  # type: ignore[arg-type]
    assert fields.get("cooldown_until_turn") == 20, (
        f"trope_resolve route must surface cooldown_until_turn so the "
        f"panel renders the cooldown bar at resolution; "
        f"fields={fields}"
    )
