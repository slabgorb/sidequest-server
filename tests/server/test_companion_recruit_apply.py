"""Tests for the narrator-driven companion recruit/dismiss apply seam.

Playtest 2026-05-06 wiring fix. The narrator hires NPCs in prose
(\"Donut joins as torchbearer\") and emits a ``companions_added`` /
``companions_dismissed`` field in its game_patch sidecar. The apply
seam is responsible for:

1. Mutating ``snapshot.companions`` (append on recruit, remove on
   dismiss).
2. Stamping ``recruited_turn`` and ``recruited_by`` from session state.
3. Emitting one ``party.recruit`` / ``party.dismiss`` watcher span per
   change so Sebastien's GM panel sees the mechanical event paired
   with the prose.

The wiring test at the bottom asserts the end-to-end production path:
``_apply_narration_result_to_snapshot`` (the production caller in
``websocket_session_handler``) accepts a ``NarrationTurnResult`` with
``companions_added`` populated and produces the same effects as the
unit-level helper.
"""

from __future__ import annotations

from typing import Any

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore
from sidequest.game.session import Companion, GameSnapshot
from sidequest.server.narration_apply import (
    _apply_companion_changes,
    _apply_narration_result_to_snapshot,
)


def _core(name: str) -> CreatureCore:
    return CreatureCore(name=name, description="X.", personality="Y.")


def _pc(name: str) -> Character:
    return Character(
        core=_core(name),
        backstory="A wanderer.",
        char_class="adventurer",
        race="human",
    )


class _RecordingHub:
    """Drop-in replacement for the watcher hub that records every event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], str]] = []

    def __call__(self, event_type: str, payload: dict[str, Any], *, component: str) -> None:
        self.events.append((event_type, payload, component))


def _patch_watcher(monkeypatch) -> _RecordingHub:
    hub = _RecordingHub()
    monkeypatch.setattr("sidequest.server.narration_apply._watcher_publish", hub)
    return hub


# ---------------------------------------------------------------------------
# Recruit
# ---------------------------------------------------------------------------


def test_recruit_appends_companion_with_turn_and_actor(monkeypatch) -> None:
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl")])
    snapshot.turn_manager.interaction = 5

    _apply_companion_changes(
        snapshot=snapshot,
        added=[
            {
                "name": "Donut",
                "role": "torchbearer",
                "description": "A grime-streaked Sünden delver.",
                "notes": "half-share, surviving recovery",
                "recruited_by": "Carl",
            }
        ],
        dismissed=[],
        acting_character_name="Carl",
        player_name="player_1",
    )

    assert len(snapshot.companions) == 1
    donut = snapshot.companions[0]
    assert donut.name == "Donut"
    assert donut.role == "torchbearer"
    assert donut.notes == "half-share, surviving recovery"
    assert donut.recruited_turn == 5
    assert donut.recruited_by == "Carl"

    recruit_events = [e for e in hub.events if e[1].get("kind") == "party.recruit"]
    assert len(recruit_events) == 1
    _, payload, component = recruit_events[0]
    assert component == "party"
    assert payload["name"] == "Donut"
    assert payload["role"] == "torchbearer"
    assert payload["roster_size_after"] == 1
    assert payload["turn_number"] == 5


def test_recruit_falls_back_to_acting_character_when_recruited_by_missing(
    monkeypatch,
) -> None:
    _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl")])
    snapshot.turn_manager.interaction = 2

    _apply_companion_changes(
        snapshot=snapshot,
        added=[{"name": "Donut", "role": "torchbearer"}],
        dismissed=[],
        acting_character_name="Carl",
        player_name="player_1",
    )

    assert snapshot.companions[0].recruited_by == "Carl"


def test_recruit_with_blank_name_is_skipped(monkeypatch) -> None:
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl")])

    _apply_companion_changes(
        snapshot=snapshot,
        added=[{"name": "   ", "role": "torchbearer"}, {"role": "porter"}],
        dismissed=[],
        acting_character_name="Carl",
        player_name="player_1",
    )

    assert snapshot.companions == []
    recruit_events = [e for e in hub.events if e[1].get("kind") == "party.recruit"]
    assert recruit_events == []


def test_duplicate_recruit_is_silent_no_op(monkeypatch) -> None:
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(
        characters=[_pc("Carl")],
        companions=[Companion(name="Donut", role="torchbearer", recruited_turn=1)],
    )

    _apply_companion_changes(
        snapshot=snapshot,
        added=[{"name": "donut", "role": "torchbearer"}],  # case mismatch
        dismissed=[],
        acting_character_name="Carl",
        player_name="player_1",
    )

    assert len(snapshot.companions) == 1
    duplicate_events = [e for e in hub.events if e[1].get("kind") == "party.recruit_duplicate"]
    assert len(duplicate_events) == 1


# ---------------------------------------------------------------------------
# Dismiss
# ---------------------------------------------------------------------------


def test_dismiss_removes_companion_and_emits_span(monkeypatch) -> None:
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(
        characters=[_pc("Carl")],
        companions=[
            Companion(name="Donut", role="torchbearer", recruited_turn=1, recruited_by="Carl")
        ],
    )
    snapshot.turn_manager.interaction = 9

    _apply_companion_changes(
        snapshot=snapshot,
        added=[],
        dismissed=["Donut"],
        acting_character_name="Carl",
        player_name="player_1",
    )

    assert snapshot.companions == []
    dismiss_events = [e for e in hub.events if e[1].get("kind") == "party.dismiss"]
    assert len(dismiss_events) == 1
    _, payload, _ = dismiss_events[0]
    assert payload["name"] == "Donut"
    assert payload["status"] == "ok"
    assert payload["served_turns"] == 8
    assert payload["roster_size_after"] == 0


def test_dismiss_unmatched_logs_and_emits_unmatched_span(monkeypatch) -> None:
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl")])

    _apply_companion_changes(
        snapshot=snapshot,
        added=[],
        dismissed=["Ghost"],
        acting_character_name="Carl",
        player_name="player_1",
    )

    assert snapshot.companions == []
    unmatched = [
        e
        for e in hub.events
        if e[1].get("kind") == "party.dismiss" and e[1].get("status") == "unmatched"
    ]
    assert len(unmatched) == 1


def test_dismiss_is_case_insensitive(monkeypatch) -> None:
    _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(
        characters=[_pc("Carl")],
        companions=[Companion(name="Donut", role="torchbearer", recruited_turn=1)],
    )

    _apply_companion_changes(
        snapshot=snapshot,
        added=[],
        dismissed=["donut"],
        acting_character_name="Carl",
        player_name="player_1",
    )

    assert snapshot.companions == []


# ---------------------------------------------------------------------------
# Wiring: production apply path consumes companions_added/dismissed.
# ---------------------------------------------------------------------------


def test_apply_narration_result_wires_companions(monkeypatch) -> None:
    """The production apply seam (callable from session_handler) MUST
    route ``companions_added`` / ``companions_dismissed`` from
    ``NarrationTurnResult`` through ``_apply_companion_changes``.

    Asserts wiring (hub gets the event, snapshot gets the companion)
    not just that the helper works in isolation.
    """
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl")])
    snapshot.turn_manager.interaction = 4

    result = NarrationTurnResult(
        narration="**Recruiter's Post**\n\nDonut steps off the slate and falls in.",
        companions_added=[
            {
                "name": "Donut",
                "role": "torchbearer",
                "description": "Grime-streaked, mostly cheerful.",
                "notes": "half-share, surviving recovery",
                "recruited_by": "Carl",
            }
        ],
    )

    # Minimal SessionRoom stand-in — narration_apply only reads attributes
    # for the path that builds confrontation/encounter resolution; for the
    # companion seam we just need *something* with the right shape.
    class _Room:
        def slot_to_player_id(self) -> dict[str, str]:
            return {}

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        player_name="Carl",
        room=_Room(),  # type: ignore[arg-type]
        pack=None,
        acting_character_name="Carl",
    )

    assert [c.name for c in snapshot.companions] == ["Donut"]
    assert snapshot.companions[0].recruited_turn == 4
    recruit_events = [e for e in hub.events if e[1].get("kind") == "party.recruit"]
    assert len(recruit_events) == 1
