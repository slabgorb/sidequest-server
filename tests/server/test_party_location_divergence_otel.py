"""Tests for the party-location-diverged OTEL detector.

Bug: sq-playtest 2026-05-12 [BUG] Location chrome stuck at first room +
per-player room state diverges. Narrator emits state_delta.location on
the acting PC's POV card only; other seated PCs' character_locations
stays at the old location. Chrome breadcrumb on those tabs reads stale.

The propagation fix is deferred to a story track (it requires either a
party_move flag in the state_delta schema or prose-marker heuristics,
both of which need design rather than fast-loop patching). This test
suite pins the OTEL detector that surfaces the divergence to Sebastien's
GM panel in the meantime — fail-loud rather than silent divergence.
"""

from __future__ import annotations

from unittest.mock import patch

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from tests._helpers.session_room import room_for


def _seated_party_snapshot(seats: dict[str, str], locations: dict[str, str]) -> GameSnapshot:
    """Build a minimal snapshot with seated PCs at the given per-PC locations."""
    snap = GameSnapshot()
    snap.turn_manager = TurnManager()
    snap.player_seats.update(seats)
    snap.character_locations.update(locations)
    return snap


def test_divergence_event_fires_when_actor_moves_alone() -> None:
    """When the actor's location diverges from co-seated PCs, the
    detector publishes state_party_location_diverged so the GM panel
    sees the symptom even before the propagation fix lands."""
    snap = _seated_party_snapshot(
        seats={"p1": "Carl", "p2": "Donut", "p3": "Katia"},
        locations={
            "Carl": "Threshold Chamber",
            "Donut": "Threshold Chamber",
            "Katia": "Threshold Chamber",
        },
    )
    result = NarrationTurnResult(narration="...", location="North Gallery")

    with patch("sidequest.server.narration_apply._watcher_publish") as wp:
        _apply_narration_result_to_snapshot(
            snap,
            result,
            player_name="Katia",
            acting_character_name="Katia",
            room=room_for(snap),
        )

    # Find the divergence event
    divergence_calls = [
        c for c in wp.call_args_list
        if c.args and c.args[0] == "state_party_location_diverged"
    ]
    assert len(divergence_calls) == 1, (
        f"Expected exactly one state_party_location_diverged event; "
        f"got {len(divergence_calls)}. All events: {[c.args[0] for c in wp.call_args_list]}"
    )
    payload = divergence_calls[0].args[1]
    assert payload["acting_character"] == "Katia"
    assert payload["new_location"] == "North Gallery"
    assert payload["seated_locations"] == {
        "Carl": "Threshold Chamber",
        "Donut": "Threshold Chamber",
        "Katia": "North Gallery",
    }
    assert payload["distinct_count"] == 2


def test_divergence_event_does_not_fire_when_party_aligned() -> None:
    """All seated PCs at the same location after the apply — no
    divergence event."""
    snap = _seated_party_snapshot(
        seats={"p1": "Carl", "p2": "Donut"},
        locations={"Carl": "North Gallery", "Donut": "North Gallery"},
    )
    # Actor stays at the agreed location (no-op move)
    result = NarrationTurnResult(narration="...", location="North Gallery")

    with patch("sidequest.server.narration_apply._watcher_publish") as wp:
        _apply_narration_result_to_snapshot(
            snap,
            result,
            player_name="Carl",
            acting_character_name="Carl",
            room=room_for(snap),
        )

    divergence_calls = [
        c for c in wp.call_args_list
        if c.args and c.args[0] == "state_party_location_diverged"
    ]
    assert divergence_calls == []


def test_divergence_event_skipped_for_solo_session() -> None:
    """Solo session has one seated PC — divergence is impossible by
    definition; the detector must skip rather than fire a single-PC
    span that would clutter Sebastien's panel with no-op events."""
    snap = _seated_party_snapshot(
        seats={"p1": "Sira"},
        locations={"Sira": "Coyote Star"},
    )
    result = NarrationTurnResult(narration="...", location="Threshold")

    with patch("sidequest.server.narration_apply._watcher_publish") as wp:
        _apply_narration_result_to_snapshot(
            snap,
            result,
            player_name="Sira",
            acting_character_name="Sira",
            room=room_for(snap),
        )

    divergence_calls = [
        c for c in wp.call_args_list
        if c.args and c.args[0] == "state_party_location_diverged"
    ]
    assert divergence_calls == []
