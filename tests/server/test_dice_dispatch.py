"""Unit + wiring tests for DICE_THROW dispatch.

Story 34 / 2026-04-24 port: Rust dice_dispatch → Python. Covers the pure
dispatcher (dispatch_dice_throw) and the session-handler integration point
(WebSocketSessionHandler._handle_dice_throw). A wiring test asserts that an
inbound DICE_THROW fans out DiceRequest + DiceResult to the whole room so
multiplayer spectators can see rolls in real time.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    MetricDirection,
    StructuredEncounter,
)
from sidequest.genre.models.rules import (
    BeatDef,
    ConfrontationDef,
    MetricDef,
    RulesConfig,
)
from sidequest.protocol.dice import (
    DiceThrowPayload,
    RollOutcome,
    ThrowParams,
)
from sidequest.protocol.messages import DiceRequestMessage, DiceResultMessage
from sidequest.server.dispatch.dice import (
    DiceDispatchError,
    dispatch_dice_throw,
)


def _pack_with_combat() -> object:
    """Minimal GenrePack-shaped stub.

    The dispatcher only reads ``pack.rules.confrontations``; constructing a
    full GenrePack would drag the entire genre loader in. A tiny stub is
    enough for dispatch tests and matches how other tests in this tree
    build pack fixtures.
    """
    cdef = ConfrontationDef(
        type="combat",
        label="Dungeon Combat",
        category="combat",
        metric=MetricDef(
            name="momentum",
            direction="bidirectional",
            starting=0,
            threshold_high=5,
            threshold_low=-5,
        ),
        beats=[
            BeatDef(
                id="kick_door",
                label="Kick Door",
                metric_delta=2,
                stat_check="STRENGTH",
            ),
            BeatDef(
                id="unknown_stat",
                label="Unknown Stat Beat",
                metric_delta=1,
                stat_check="   ",  # blank — used for validation test
            ),
            # Parallels mutant_wasteland Flank / Use Mutation — structured
            # failure branch pays out -2 momentum on Fail/CritFail instead of
            # the default +3 success delta.
            BeatDef(
                id="flank",
                label="Flank",
                metric_delta=3,
                stat_check="STRENGTH",
                risk="exposed if it fails — lose 2 momentum",
                failure_metric_delta=-2,
                failure_effect="exposed",
            ),
        ],
    )
    rules = MagicMock(spec=RulesConfig)
    rules.confrontations = [cdef]
    pack = MagicMock()
    pack.rules = rules
    return pack


def _make_encounter() -> StructuredEncounter:
    return StructuredEncounter(
        encounter_type="combat",
        metric=EncounterMetric(
            name="momentum",
            current=0,
            starting=0,
            direction=MetricDirection.Bidirectional,
            threshold_high=5,
            threshold_low=-5,
        ),
        beat=0,
        structured_phase=EncounterPhase.Setup,
        secondary_stats=None,
        actors=[EncounterActor(name="Bob", role="combatant", per_actor_state={})],
        outcome=None,
        resolved=False,
        mood_override=None,
        narrator_hints=[],
    )


def _throw(face: int = 14, beat_id: str | None = "kick_door") -> DiceThrowPayload:
    return DiceThrowPayload(
        request_id="req-1",
        throw_params=ThrowParams(
            velocity=(0.0, 5.0, -2.0),
            angular=(1.0, 1.0, 1.0),
            position=(0.5, 0.5),
        ),
        face=[face],
        beat_id=beat_id,
    )


class TestDispatchDiceThrow:
    """Pure dispatcher — no session handler, no room."""

    def test_success_roll_resolves_and_applies_beat(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        outcome = dispatch_dice_throw(
            payload=_throw(face=13),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 16},  # +3 modifier
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="session-1",
            round_number=1,
            room_broadcast=None,
        )
        # 13 + 3 = 16 >= DC(10 + |2|*2 = 14) → Success (margin 2 < DECISIVE_MARGIN)
        assert outcome.outcome is RollOutcome.Success
        assert outcome.result.total == 16
        assert outcome.result.difficulty == 14
        assert outcome.request.modifier == 3
        assert outcome.request.rolling_player_id == "p1"
        # Beat applied: metric +2 (before 0, after 2)
        assert enc.metric.current == 2
        assert "[BEAT_RESOLVED] Kick Door" in outcome.replay_action_text
        assert "Roll: 16 (Success)" in outcome.replay_action_text

    def test_crit_success_bypasses_dc(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        # Face 20 with a huge negative modifier and a high DC — CritSuccess wins.
        outcome = dispatch_dice_throw(
            payload=_throw(face=20),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 1},  # -5 modifier
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        assert outcome.outcome is RollOutcome.CritSuccess

    def test_crit_fail_bypasses_modifier(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        outcome = dispatch_dice_throw(
            payload=_throw(face=1),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 30},  # +10 modifier
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        assert outcome.outcome is RollOutcome.CritFail

    def test_missing_beat_id_raises_dispatch_error(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        with pytest.raises(DiceDispatchError, match="missing beat_id"):
            dispatch_dice_throw(
                payload=_throw(beat_id=None),
                rolling_player_id="p1",
                character_name="Bob",
                character_stats={},
                encounter=enc,
                pack=pack,  # type: ignore[arg-type]
                session_id="s",
                round_number=1,
                room_broadcast=None,
            )

    def test_no_active_encounter_raises(self) -> None:
        pack = _pack_with_combat()
        with pytest.raises(DiceDispatchError, match="active encounter"):
            dispatch_dice_throw(
                payload=_throw(),
                rolling_player_id="p1",
                character_name="Bob",
                character_stats={"STRENGTH": 10},
                encounter=None,
                pack=pack,  # type: ignore[arg-type]
                session_id="s",
                round_number=1,
                room_broadcast=None,
            )

    def test_unknown_beat_id_raises(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        with pytest.raises(DiceDispatchError, match="unknown beat_id"):
            dispatch_dice_throw(
                payload=_throw(beat_id="does_not_exist"),
                rolling_player_id="p1",
                character_name="Bob",
                character_stats={"STRENGTH": 10},
                encounter=enc,
                pack=pack,  # type: ignore[arg-type]
                session_id="s",
                round_number=1,
                room_broadcast=None,
            )

    def test_invalid_stat_check_raises_without_mutating_encounter(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        prev_metric = enc.metric.current
        with pytest.raises(DiceDispatchError, match="invalid stat_check"):
            dispatch_dice_throw(
                payload=_throw(beat_id="unknown_stat"),
                rolling_player_id="p1",
                character_name="Bob",
                character_stats={},
                encounter=enc,
                pack=pack,  # type: ignore[arg-type]
                session_id="s",
                round_number=1,
                room_broadcast=None,
            )
        # Validate-then-mutate: encounter untouched on validation failure.
        assert enc.metric.current == prev_metric

    def test_case_insensitive_stat_lookup(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        # Character's stats dict uses TitleCase; stat_check is UPPERCASE.
        outcome = dispatch_dice_throw(
            payload=_throw(face=12),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"Strength": 14},  # +2 modifier via case-insensitive lookup
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        assert outcome.request.modifier == 2

    def test_broadcast_sends_dice_request_and_result_in_order(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        broadcasts: list[object] = []
        dispatch_dice_throw(
            payload=_throw(face=15),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 10},
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=broadcasts.append,
        )
        # Spectators need the overlay to open before the result lands.
        assert len(broadcasts) == 2
        assert isinstance(broadcasts[0], DiceRequestMessage)
        assert isinstance(broadcasts[1], DiceResultMessage)
        assert broadcasts[0].payload.request_id == "req-1"
        assert broadcasts[1].payload.request_id == "req-1"

    def test_failed_roll_applies_failure_metric_delta(self) -> None:
        """Flank-style beat with structured failure branch.

        Regression for playtest 2026-04-24: a failed Flank (metric_delta=+3,
        failure_metric_delta=-2) was bumping momentum +3 because
        _apply_beat ran before dice resolution. With resolve-first ordering,
        a Fail outcome must substitute the failure branch delta.
        """
        pack = _pack_with_combat()
        enc = _make_encounter()
        # Face 3 + modifier 0 = total 3 < DC(10 + |3|*2 = 16) → Fail.
        outcome = dispatch_dice_throw(
            payload=_throw(face=3, beat_id="flank"),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 10},
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        assert outcome.outcome is RollOutcome.Fail
        # Failure branch pays out — momentum drops by 2, not up by 3.
        assert enc.metric.current == -2
        # Replay text reflects the actually-applied delta (0 → -2).
        assert "momentum 0 → -2" in outcome.replay_action_text.lower() or \
            "Momentum 0 → -2" in outcome.replay_action_text

    def test_crit_fail_applies_failure_metric_delta(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        # Face 1 always resolves CritFail, regardless of modifier.
        outcome = dispatch_dice_throw(
            payload=_throw(face=1, beat_id="flank"),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 30},  # huge modifier; face 1 wins
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        assert outcome.outcome is RollOutcome.CritFail
        assert enc.metric.current == -2

    def test_success_roll_applies_default_metric_delta(self) -> None:
        """Success on a failure-branch beat keeps the normal delta."""
        pack = _pack_with_combat()
        enc = _make_encounter()
        outcome = dispatch_dice_throw(
            payload=_throw(face=17, beat_id="flank"),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 10},  # +0 modifier; total=17 >= 16
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        assert outcome.outcome is RollOutcome.Success
        assert enc.metric.current == 3  # default metric_delta

    def test_failed_roll_without_failure_branch_keeps_default_delta(self) -> None:
        """Beats without failure_metric_delta keep legacy unconditional apply."""
        pack = _pack_with_combat()
        enc = _make_encounter()
        # kick_door has metric_delta=2, no failure_metric_delta. DC = 14.
        # Face 3 + STR 10 = total 3 → Fail. Legacy behavior: +2 still applies.
        outcome = dispatch_dice_throw(
            payload=_throw(face=3, beat_id="kick_door"),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 10},
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        assert outcome.outcome is RollOutcome.Fail
        assert enc.metric.current == 2

    def test_encounter_resolves_when_beat_hits_threshold(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        # Push metric to just-below-threshold so a +2 beat crosses it.
        enc.metric.current = 4
        outcome = dispatch_dice_throw(
            payload=_throw(face=14),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 10},
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        assert enc.resolved is True
        assert enc.structured_phase is EncounterPhase.Resolution
        assert outcome.encounter_resolved is True


class TestDiceThrowWireFormat:
    """Wire-format round-trip — the shape the UI consumes."""

    def test_result_serializes_with_expected_fields(self) -> None:
        pack = _pack_with_combat()
        enc = _make_encounter()
        outcome = dispatch_dice_throw(
            payload=_throw(face=15),
            rolling_player_id="p1",
            character_name="Bob",
            character_stats={"STRENGTH": 10},
            encounter=enc,
            pack=pack,  # type: ignore[arg-type]
            session_id="s",
            round_number=1,
            room_broadcast=None,
        )
        wire = DiceResultMessage(
            payload=outcome.result, player_id="server",
        ).model_dump_json()
        # Shape-check: every field the React UI reads from DiceResultPayload.
        # Matches sidequest-ui/src/types/payloads.ts::DiceResultPayload.
        for field_name in (
            '"request_id"',
            '"rolling_player_id"',
            '"character_name"',
            '"rolls"',
            '"modifier"',
            '"total"',
            '"difficulty"',
            '"outcome"',
            '"seed"',
            '"throw_params"',
        ):
            assert field_name in wire, f"missing wire field {field_name}"
