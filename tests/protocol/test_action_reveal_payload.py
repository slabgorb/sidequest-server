"""Tests for ACTION_REVEAL protocol types."""

import pytest
from pydantic import ValidationError

from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import (
    ActionRevealMessage,
    ActionRevealPayload,
    ActionRevealStatus,
    GameMessage,
)


def test_composing_payload_round_trips() -> None:
    payload = ActionRevealPayload(
        player_id="p1",
        character_name="Alex",
        status=ActionRevealStatus.COMPOSING,
        action="I creep along the rafters",
        aside=False,
        seq=3,
        round=7,
    )
    msg = ActionRevealMessage(payload=payload)
    dumped = msg.model_dump(mode="json")
    assert dumped["type"] == "ACTION_REVEAL"
    assert dumped["payload"]["status"] == "composing"
    assert dumped["payload"]["seq"] == 3
    rehydrated = ActionRevealMessage.model_validate(dumped)
    assert rehydrated.payload.action == "I creep along the rafters"


def test_status_must_be_known_value() -> None:
    with pytest.raises(ValidationError):
        ActionRevealPayload(
            player_id="p1",
            character_name="Alex",
            status="banana",  # type: ignore[arg-type]
            action="hi",
            aside=False,
            seq=0,
            round=0,
        )


def test_action_can_be_empty_when_cleared() -> None:
    payload = ActionRevealPayload(
        player_id="p1",
        character_name="Alex",
        status=ActionRevealStatus.CLEARED,
        action="",
        aside=False,
        seq=99,
        round=7,
    )
    assert payload.action == ""


def test_seq_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        ActionRevealPayload(
            player_id="p1",
            character_name="Alex",
            status=ActionRevealStatus.COMPOSING,
            action="x",
            aside=False,
            seq=-1,
            round=0,
        )


def test_action_reveal_routes_through_game_message_union() -> None:
    raw = {
        "type": "ACTION_REVEAL",
        "payload": {
            "player_id": "p1",
            "character_name": "Alex",
            "status": "composing",
            "action": "I creep along the rafters",
            "aside": False,
            "seq": 0,
            "round": 1,
        },
        "player_id": "p1",
    }
    parsed = GameMessage.model_validate(raw)
    assert parsed.type == MessageType.ACTION_REVEAL
    assert isinstance(parsed.root, ActionRevealMessage)
    assert parsed.payload.status == ActionRevealStatus.COMPOSING
