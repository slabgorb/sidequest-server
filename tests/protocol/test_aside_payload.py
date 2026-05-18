"""ADR-107 — ASIDE_ANSWER message type + AsideAnswerPayload (RED, story 50-25).

Plan: docs/superpowers/plans/2026-05-17-aside-channel.md Task 1.
These fail until Dev adds the enum member + payload (GREEN).
"""

from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import AsideAnswerPayload


def test_aside_answer_message_type_exists():
    assert MessageType.ASIDE_ANSWER == "ASIDE_ANSWER"


def test_aside_answer_payload_roundtrips():
    p = AsideAnswerPayload(
        asker_id="Hiken",
        question="can I wade or must I be carried?",
        answer="Knee-deep on you, Hiken. Wading's slow but no carry needed.",
        grounded_on=["character.size", "region.water_depth"],
        round=7,
    )
    dumped = p.model_dump()
    assert dumped["asker_id"] == "Hiken"
    assert dumped["grounded_on"] == ["character.size", "region.water_depth"]
    assert AsideAnswerPayload(**dumped).answer.startswith("Knee-deep")


def test_aside_answer_payload_defaults_are_safe():
    p = AsideAnswerPayload()
    assert p.asker_id == "" and p.answer == "" and p.grounded_on == [] and p.round == 0
