"""Tone/axis tension OTEL wiring — sprint 3 cold-subsystem audit.

Pre-audit: ``game/tension_tracker.py`` emitted zero watcher events.
Pacing-tension changes (drama_weight, action_tension, stakes_tension)
were invisible to the GM panel — Sebastien's mechanical-visibility lens
needs to see when classifications fire and how the axes evolve.

Test pins the new ``tension:round_observed`` event published from
``TensionTracker.observe`` on every combat-round observation.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from sidequest.game.tension_tracker import (
    DamageEvent,
    RoundResult,
    TensionTracker,
)


@pytest.fixture
def captured_watcher_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Intercept watcher_hub.publish_event for tension events.

    The tension_tracker module imports publish_event lazily inside
    observe() (``from sidequest.telemetry.watcher_hub import
    publish_event``), so monkeypatch the source attribute — not a
    re-exported alias — to catch the lazy resolution."""
    captured: list[dict[str, Any]] = []

    def _capture(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append(
            {"event_type": event_type, "fields": fields, "component": component, "severity": severity}
        )

    from sidequest.telemetry import watcher_hub as hub_mod

    monkeypatch.setattr(hub_mod, "publish_event", _capture)
    yield captured


def _tension_events(captured: list[dict]) -> list[dict]:
    return [
        e
        for e in captured
        if e["component"] == "tension"
        and e["event_type"] == "state_transition"
        and e["fields"].get("op") == "round_observed"
    ]


def _round(damage_total: int = 0) -> RoundResult:
    if damage_total == 0:
        damage_events: list[DamageEvent] = []
    else:
        damage_events = [
            DamageEvent(attacker="atk", target="tgt", damage=damage_total, round=1)
        ]
    return RoundResult(
        round=1,
        damage_events=damage_events,
        effects_applied=[],
        effects_expired=[],
    )


def test_observe_publishes_round_observed_event(captured_watcher_events: list[dict]) -> None:
    """A single observe() call publishes exactly one tension event with
    the current axis values and the classification kind."""
    tracker = TensionTracker()
    tracker.observe(_round(), killed=None, lowest_hp_ratio=None)

    events = _tension_events(captured_watcher_events)
    assert len(events) == 1, (
        f"expected exactly one tension:round_observed event, got {len(events)}: "
        f"{[e['fields'] for e in captured_watcher_events]}"
    )
    fields = events[0]["fields"]
    assert fields["classification"] == "Boring"  # zero damage, no kill, no effects
    # Boring increments action_tension via record_event before observe
    # returns; we don't pin the exact constant here (genre-tunable),
    # only that the axis values are reported.
    assert isinstance(fields["action_tension"], float)
    assert fields["stakes_tension"] == 0.0
    assert isinstance(fields["drama_weight"], float)
    assert fields["boring_streak"] == 1
    assert fields["active_spike"] == 0.0


def test_observe_dramatic_round_emits_event_with_event_label(
    captured_watcher_events: list[dict],
) -> None:
    """A kill is always dramatic (per classify_round). The event label
    must round-trip through the watcher payload so the GM panel can
    plot WHICH dramatic event fired (Kill, NearMiss, etc.)."""
    tracker = TensionTracker()
    tracker.observe(_round(damage_total=5), killed="some_npc", lowest_hp_ratio=0.0)

    events = _tension_events(captured_watcher_events)
    assert len(events) == 1
    fields = events[0]["fields"]
    assert fields["classification"] == "Dramatic"
    assert fields["event"]  # non-empty event label set on dramatic classification
    # Active spike is non-zero immediately after the spike injection.
    assert fields["active_spike"] > 0.0
    assert fields["drama_weight"] > 0.0


def test_multiple_observes_emit_one_event_each(captured_watcher_events: list[dict]) -> None:
    """N observe() calls produce N tension events. Pins per-round
    cadence — no batching, no debouncing."""
    tracker = TensionTracker()
    for _ in range(3):
        tracker.observe(_round(), killed=None, lowest_hp_ratio=None)

    events = _tension_events(captured_watcher_events)
    assert len(events) == 3
    # boring_streak monotonically increases through the sequence.
    streaks = [e["fields"]["boring_streak"] for e in events]
    assert streaks == [1, 2, 3]
