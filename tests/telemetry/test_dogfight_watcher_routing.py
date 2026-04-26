"""End-to-end watcher routing test for the dogfight span family (T4).

T3 emitted the three sealed-letter OTEL spans
(``dogfight.confrontation_started``, ``dogfight.maneuver_committed``,
``dogfight.cell_resolved``) and proved they reach an in-memory OTEL
exporter. The GM panel, however, doesn't read OTEL spans directly — it
reads ``WatcherEvent``s. This test closes that gap: it drives the real
``resolve_sealed_letter_lookup`` handler with a minimal red/blue
encounter, hooks a ``WatcherSpanProcessor`` onto a local
``TracerProvider``, and asserts the typed ``state_transition`` events
land on a hub subscriber with the payload shape the dashboard's
Subsystems tab consumes.

If anyone removes the dogfight ``SPAN_ROUTES`` entries (or breaks the
on_end translator), this test fails before the GM panel goes dark.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    StructuredEncounter,
)
from sidequest.genre.models.rules import InteractionCell, InteractionTable
from sidequest.server.dispatch.sealed_letter import resolve_sealed_letter_lookup
from sidequest.server.watcher import WatcherSpanProcessor
from sidequest.telemetry import spans as spans_module
from sidequest.telemetry.spans import (
    SPAN_DOGFIGHT_CELL_RESOLVED,
    SPAN_DOGFIGHT_CONFRONTATION_STARTED,
    SPAN_DOGFIGHT_MANEUVER_COMMITTED,
    SPAN_ROUTES,
)
from sidequest.telemetry.watcher_hub import WatcherHub

# ---------------------------------------------------------------------------
# Fixture helpers (mirrors tests/server/dispatch/test_sealed_letter.py)
# ---------------------------------------------------------------------------


def _make_encounter() -> StructuredEncounter:
    actors = [
        EncounterActor(
            name="Red Pilot",
            role="red",
            side="player",
            per_actor_state={},
        ),
        EncounterActor(
            name="Blue Pilot",
            role="blue",
            side="opponent",
            per_actor_state={},
        ),
    ]
    return StructuredEncounter(
        encounter_type="dogfight",
        player_metric=EncounterMetric(name="hits", current=0, threshold=3),
        opponent_metric=EncounterMetric(name="hits", current=0, threshold=3),
        actors=actors,
    )


def _make_table() -> InteractionTable:
    cell = InteractionCell(
        pair=["straight", "loop"],
        name="Blue reverses onto Red's six",
        shape="passive vs offense",
        red_view={
            "target_bearing": "06",
            "closure": "opening",
            "gun_solution": False,
        },
        blue_view={
            "target_bearing": "12",
            "closure": "opening",
            "gun_solution": True,
        },
        narration_hint="Blue pulls the loop, Red is in the gunsight.",
    )
    return InteractionTable(
        version="0.1.0",
        starting_state="merge",
        maneuvers_consumed=["straight", "bank", "loop", "kill_rotation"],
        cells=[cell],
    )


class _CapturingSubscriber:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.events.append(data)


# ---------------------------------------------------------------------------
# Static routing assertions (cheap; complement the runtime test below)
# ---------------------------------------------------------------------------


def test_dogfight_spans_have_routes_registered() -> None:
    """Every dogfight span must have a SPAN_ROUTES entry — without one,
    only the firehose ``agent_span_close`` would fire and the typed
    Subsystems tab would stay dark for dogfights."""
    for name in (
        SPAN_DOGFIGHT_CONFRONTATION_STARTED,
        SPAN_DOGFIGHT_MANEUVER_COMMITTED,
        SPAN_DOGFIGHT_CELL_RESOLVED,
    ):
        assert name in SPAN_ROUTES, f"missing SPAN_ROUTES entry for {name!r}"
        route = SPAN_ROUTES[name]
        assert route.event_type == "state_transition"
        assert route.component == "dogfight"


# ---------------------------------------------------------------------------
# End-to-end wiring test: real handler → on_end → hub subscriber
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sealed_letter_emits_dogfight_watcher_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the real sealed-letter handler and assert all three typed
    ``state_transition`` events land on a watcher-hub subscriber with the
    expected component, op, and field payload.

    This is the integration check that proves spans → WatcherSpanProcessor
    → WatcherHub → subscriber is intact for the dogfight subsystem.
    """
    hub = WatcherHub()
    hub.bind_loop(asyncio.get_running_loop())
    sub = _CapturingSubscriber()
    await hub.subscribe(sub)  # type: ignore[arg-type]

    # Local TracerProvider with the WatcherSpanProcessor — same pattern
    # used by test_state_transition_fires_on_npc_auto_register.
    provider = TracerProvider()
    provider.add_span_processor(WatcherSpanProcessor(hub))
    local_tracer = provider.get_tracer("test-dogfight-watcher-routing")
    monkeypatch.setattr(spans_module, "tracer", lambda: local_tracer)

    encounter = _make_encounter()
    table = _make_table()

    outcome = resolve_sealed_letter_lookup(
        encounter,
        {"red": "straight", "blue": "loop"},
        table,
    )
    assert outcome.cell_name == "Blue reverses onto Red's six"

    # Allow the cross-thread broadcast hop to flush.
    await asyncio.sleep(0.05)

    typed = [
        e for e in sub.events
        if e["event_type"] == "state_transition"
        and e["component"] == "dogfight"
    ]
    ops = [e["fields"].get("op") for e in typed]
    assert ops == [
        "confrontation_started",
        "maneuver_committed",
        "maneuver_committed",
        "cell_resolved",
    ], f"unexpected dogfight op sequence: {ops} (events={sub.events})"

    # confrontation_started carries both actor names and the encounter type.
    started = typed[0]["fields"]
    assert started["field"] == "dogfight"
    assert started["encounter_type"] == "dogfight"
    assert started["red_actor"] == "Red Pilot"
    assert started["blue_actor"] == "Blue Pilot"

    # The two maneuver_committed events carry per-actor commits in order
    # (red first, blue second — matches the handler's emission order).
    red_commit = typed[1]["fields"]
    assert red_commit["actor"] == "Red Pilot"
    assert red_commit["maneuver"] == "straight"
    assert red_commit["role"] == "red"

    blue_commit = typed[2]["fields"]
    assert blue_commit["actor"] == "Blue Pilot"
    assert blue_commit["maneuver"] == "loop"
    assert blue_commit["role"] == "blue"

    # cell_resolved carries cell metadata + the resolved maneuvers — the
    # GM panel uses these to plot the timeline cell with the same name
    # the narrator sees.
    resolved = typed[3]["fields"]
    assert resolved["cell_name"] == "Blue reverses onto Red's six"
    assert resolved["shape"] == "passive vs offense"
    assert resolved["red_maneuver"] == "straight"
    assert resolved["blue_maneuver"] == "loop"
    # No extend-and-return because Blue scored gun_solution=True.
    assert resolved["extend_and_return_triggered"] is False

    # Augment-not-replace: every typed event must coexist with its
    # firehose ``agent_span_close`` sibling (same invariant the
    # projection / quest / npc tests check).
    flat_names = {
        e["fields"].get("name") for e in sub.events
        if e["event_type"] == "agent_span_close"
    }
    for span_name in (
        SPAN_DOGFIGHT_CONFRONTATION_STARTED,
        SPAN_DOGFIGHT_MANEUVER_COMMITTED,
        SPAN_DOGFIGHT_CELL_RESOLVED,
    ):
        assert span_name in flat_names, (
            f"flat agent_span_close missing for {span_name!r}; firehose "
            f"got: {sorted(flat_names)}"
        )
