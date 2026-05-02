"""SOUL "The Test" gate: PC-side beat_selections from narrator extraction
must NEVER fire on the confrontation engine.

Background — Playtest 2026-04-26 [S2-BUG]:
George's turn 7 narration ended with the orchestrator extracting
``beat_selections=[Paul.concede_point, Dispatcher.persuade]``. Paul never
clicked anything; the system invented a mechanical action for him from
George's prose. Per ``SOUL.md`` "The Test", this is a flat-out failure —
"if a response includes the player doing something they didn't ask to do,
it's wrong."

Fix contract (this file is the wiring lock):

* Narrator-extracted ``beat_selections`` reaching
  ``_apply_narration_result_to_snapshot`` MUST be filtered to drop every
  selection whose actor has ``side == "player"``.
* Opponent-side beats (NPCs, Dispatcher, hostiles) STILL fire from
  narrator extraction — NPCs don't have the same agency contract.
* Each rejected PC beat emits a ``confrontation.inferred_pc_beat_rejected``
  watcher event and a ``encounter.beat_skipped`` span — the GM panel lie
  detector. Without OTEL, you can't tell whether the gate is silently
  failing.
* The legitimate PC beat path is ``sidequest.server.dispatch.dice
  .dispatch_dice_throw``, which is driven by an explicit DICE_THROW frame
  carrying a ``beat_id`` chosen by THAT PC on THEIR socket. That path is
  unchanged.

Tests in this file:

1. ``test_inferred_pc_beat_from_peer_narration_is_rejected`` — George's
    narration tries to fire Paul's ``concede_point``. Paul's metric
    must not move; a watcher event must record the rejection.
2. ``test_inferred_own_pc_beat_from_narration_is_rejected`` — even the
    narrating player's own PC beat is rejected when it comes from prose.
    Otherwise Sebastien (mechanics-first) can "trick" the parser by
    writing prose to commit a beat without dice.
3. ``test_npc_beat_from_narration_still_fires`` — opponent-side beats
    still apply through narrator extraction (NPCs don't have agency).
4. ``test_explicit_action_path_still_advances_pc_metric`` — wiring
    test. The legitimate ``dispatch_dice_throw`` path still advances
    the player dial. Proves the gate isn't over-broad.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from sidequest.agents.orchestrator import (
    BeatSelection,
    NarrationTurnResult,
    NpcMention,
)
from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    StructuredEncounter,
)
from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.protocol.dice import RollOutcome
from tests._helpers.session_room import room_for


def _make_snapshot() -> GameSnapshot:
    """Story 45-9: dispatch_dice_throw now requires a snapshot."""
    return GameSnapshot(
        genre_slug="test",
        world_slug="test",
        turn_manager=TurnManager(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def two_pc_negotiation_setup(synthetic_two_dial_pack):
    """Negotiation-style encounter with TWO player-side actors and one
    opponent-side actor — mirrors the Beatles 4-player playtest config
    (multiple PCs share a confrontation, one of them narrates).
    """
    enc = StructuredEncounter(
        encounter_type="combat",  # synthetic_two_dial_pack only defines 'combat'
        player_metric=EncounterMetric(
            name="momentum",
            current=0,
            starting=0,
            threshold=10,
        ),
        opponent_metric=EncounterMetric(
            name="momentum",
            current=0,
            starting=0,
            threshold=10,
        ),
        actors=[
            EncounterActor(name="George", role="combatant", side="player"),
            EncounterActor(name="Paul", role="combatant", side="player"),
            EncounterActor(name="Dispatcher", role="hostile", side="opponent"),
        ],
    )
    return enc, synthetic_two_dial_pack


@pytest.fixture
def captured_watcher_events(monkeypatch) -> Iterator[list[dict[str, Any]]]:
    """Intercept ``narration_apply._watcher_publish`` calls so tests can
    assert on the rejection events without touching the real hub.
    """
    captured: list[dict[str, Any]] = []

    def _capture(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append(
            {
                "event_type": event_type,
                "fields": fields,
                "component": component,
                "severity": severity,
            }
        )

    from sidequest.server import narration_apply

    monkeypatch.setattr(narration_apply, "_watcher_publish", _capture)
    yield captured


# ---------------------------------------------------------------------------
# Test 1: peer narration cannot fire another PC's beat
# ---------------------------------------------------------------------------


def test_inferred_pc_beat_from_peer_narration_is_rejected(
    snapshot_with_pack,
    two_pc_negotiation_setup,
    captured_watcher_events,
):
    """George narrates "Paul concedes the point" — narrator extracts a
    beat for Paul. Paul's player_metric MUST NOT move. The gate emits a
    ``confrontation.inferred_pc_beat_rejected`` watcher event so the GM
    panel can see the rejection.
    """
    from sidequest.server.narration_apply import (
        _apply_narration_result_to_snapshot,
    )

    snap, _pack = snapshot_with_pack
    enc, pack = two_pc_negotiation_setup
    snap.encounter = enc

    result = NarrationTurnResult(
        narration="George leans forward; Paul nods, conceding the point.",
        beat_selections=[
            # The bug: narrator extracted Paul's beat from George's prose.
            BeatSelection(
                actor="Paul",
                beat_id="defend",
                outcome=RollOutcome.Success,
                target=None,
            ),
        ],
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="George",
        pack=pack,
        room=room_for(snap),
    )

    # Player dial untouched — Paul's beat was rejected.
    assert snap.encounter is not None
    assert snap.encounter.player_metric.current == 0, (
        "PC beat inferred from peer narration must NOT advance player_metric "
        "(SOUL 'The Test' violation)"
    )

    # Watcher event recorded the rejection — lie detector intact.
    rejections = [
        e
        for e in captured_watcher_events
        if e["event_type"] == "state_transition"
        and e["fields"].get("op") == "inferred_pc_beat_rejected"
    ]
    assert len(rejections) == 1, (
        f"expected exactly one inferred_pc_beat_rejected event; got "
        f"{[e['fields'].get('op') for e in captured_watcher_events]}"
    )
    fields = rejections[0]["fields"]
    assert fields["actor"] == "Paul"
    assert fields["beat_id"] == "defend"
    assert fields["narrating_player"] == "George"
    assert fields["source"] == "peer_narration"


# ---------------------------------------------------------------------------
# Test 2: narrating player's OWN PC beat is also rejected
# ---------------------------------------------------------------------------


def test_inferred_own_pc_beat_from_narration_is_rejected(
    snapshot_with_pack,
    two_pc_negotiation_setup,
    captured_watcher_events,
):
    """Even when the narrator emits a beat for the narrating PC, the
    gate rejects it. PC beats MUST come through DICE_THROW dispatch.
    Otherwise Sebastien (mechanics-first) can write prose like "I attack
    the goblin (CritSuccess)" and bypass the dice — defeating the
    explicit-consent design.
    """
    from sidequest.server.narration_apply import (
        _apply_narration_result_to_snapshot,
    )

    snap, _pack = snapshot_with_pack
    enc, pack = two_pc_negotiation_setup
    snap.encounter = enc

    result = NarrationTurnResult(
        narration="George rolls forward, attacking the Dispatcher.",
        beat_selections=[
            BeatSelection(
                actor="George",
                beat_id="attack",
                outcome=RollOutcome.Success,
                target=None,
            ),
        ],
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="George",
        pack=pack,
        room=room_for(snap),
    )

    assert snap.encounter is not None
    assert snap.encounter.player_metric.current == 0, (
        "Narrating PC's own beat from prose must NOT advance player_metric "
        "— PC beats require an explicit DICE_THROW frame, not narration"
    )

    rejections = [
        e
        for e in captured_watcher_events
        if e["event_type"] == "state_transition"
        and e["fields"].get("op") == "inferred_pc_beat_rejected"
    ]
    assert len(rejections) == 1
    assert rejections[0]["fields"]["actor"] == "George"
    assert rejections[0]["fields"]["source"] == "narrator_self"


# ---------------------------------------------------------------------------
# Test 3: NPC beats from narration STILL fire (don't over-restrict)
# ---------------------------------------------------------------------------


def test_npc_beat_from_narration_still_fires(
    snapshot_with_pack,
    two_pc_negotiation_setup,
    captured_watcher_events,
):
    """Opponent-side actors (Dispatcher, hostiles) DO have their beats
    applied from narrator extraction — NPCs don't have the same agency
    contract as PCs. Without this, the engine would have to invent a
    separate dispatch path for every NPC turn.
    """
    from sidequest.server.narration_apply import (
        _apply_narration_result_to_snapshot,
    )

    snap, _pack = snapshot_with_pack
    enc, pack = two_pc_negotiation_setup
    snap.encounter = enc

    result = NarrationTurnResult(
        narration="The Dispatcher levels its blade and strikes.",
        beat_selections=[
            BeatSelection(
                actor="Dispatcher",
                beat_id="attack",
                outcome=RollOutcome.Success,
                target=None,
            ),
        ],
        npcs_present=[
            NpcMention(name="Dispatcher", side="opponent", role="hostile"),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="George",
        pack=pack,
        room=room_for(snap),
    )

    # Opponent dial advanced — NPC beat applied normally.
    # 'attack' is kind=strike, base=2 → opponent's own metric +2.
    assert snap.encounter is not None
    assert snap.encounter.opponent_metric.current == 2, (
        "Opponent-side beats from narration MUST still apply — over-"
        "restricting would leave NPCs inert"
    )

    # No PC rejection events.
    rejections = [
        e
        for e in captured_watcher_events
        if e["event_type"] == "state_transition"
        and e["fields"].get("op") == "inferred_pc_beat_rejected"
    ]
    assert rejections == [], (
        f"Opponent beat must not be misclassified as a PC rejection; got rejections {rejections}"
    )


# ---------------------------------------------------------------------------
# Test 4: wiring — explicit DICE_THROW dispatch still advances PC metric
# ---------------------------------------------------------------------------


def test_explicit_action_path_still_advances_pc_metric(
    snapshot_with_pack,
    two_pc_negotiation_setup,
):
    """Wiring guard: the legitimate PC beat path
    (``dispatch_dice_throw`` driven by a player's explicit DICE_THROW
    frame) is unchanged. The gate is scoped to narrator-extracted beats
    only — proves we haven't accidentally blocked the consent-bearing
    route.

    This test drives ``dispatch_dice_throw`` directly to simulate the
    DICE_THROW socket frame (the real production path) and asserts the
    player_metric advances.
    """
    from sidequest.protocol.dice import DiceThrowPayload, ThrowParams
    from sidequest.server.dispatch.dice import dispatch_dice_throw

    snap, _pack = snapshot_with_pack
    enc, pack = two_pc_negotiation_setup
    snap.encounter = enc

    payload = DiceThrowPayload(
        request_id="r-george-1",
        throw_params=ThrowParams(
            velocity=(0, 0, 0),
            angular=(0, 0, 0),
            position=(0, 0),
        ),
        # Single d20 face (pool size = 1 for the default difficulty
        # gate). 18 + STR mod is comfortably over DC 10.
        face=[18],
        beat_id="attack",
    )
    dispatch_dice_throw(
        payload=payload,
        rolling_player_id="p-george",
        character_name="George",
        character_stats={
            "STR": 14,
            "DEX": 10,
            "CON": 10,
            "INT": 10,
            "WIS": 10,
            "CHA": 10,
        },
        encounter=enc,
        pack=pack,
        genre_slug="test",
        session_id="s1",
        round_number=1,
        room_broadcast=None,
        snapshot=_make_snapshot(),
    )

    # PC metric advanced — explicit action consent bears the apply.
    assert snap.encounter is not None
    assert snap.encounter.player_metric.current > 0, (
        "Explicit dispatch_dice_throw path MUST still advance PC metric — gate was over-broad"
    )
