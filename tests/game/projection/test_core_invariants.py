"""CoreInvariantStage — GM sees truth."""
from __future__ import annotations

import json

from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.invariants import CoreInvariantStage
from sidequest.game.projection.view import SessionGameStateView


def _view() -> SessionGameStateView:
    return SessionGameStateView(
        gm_player_id="gm",
        player_id_to_character={"alice": "alice_char", "gm": None},  # type: ignore[dict-item]
    )


def test_gm_sees_canonical_short_circuits() -> None:
    stage = CoreInvariantStage()
    env = MessageEnvelope(kind="STATE_UPDATE", payload_json='{"hp":10}', origin_seq=1)
    outcome = stage.evaluate(envelope=env, view=_view(), player_id="gm")
    assert outcome.terminal is True
    assert outcome.decision is not None
    assert outcome.decision.include is True
    assert outcome.decision.payload_json == '{"hp":10}'


def test_non_gm_passes_through_gm_invariant() -> None:
    stage = CoreInvariantStage()
    env = MessageEnvelope(kind="NARRATION", payload_json='{"text":"hi"}', origin_seq=2)
    outcome = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert outcome.terminal is False
    assert outcome.decision is None


def test_secret_note_routes_only_to_recipient() -> None:
    stage = CoreInvariantStage()
    payload = json.dumps({"to": "alice", "text": "psst"})
    env = MessageEnvelope(kind="SECRET_NOTE", payload_json=payload, origin_seq=3)

    out_alice = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert out_alice.terminal is True
    assert out_alice.decision is not None
    assert out_alice.decision.include is True

    out_bob = stage.evaluate(envelope=env, view=_view(), player_id="bob")
    assert out_bob.terminal is True
    assert out_bob.decision is not None
    assert out_bob.decision.include is False


def test_dice_request_to_field_with_list() -> None:
    stage = CoreInvariantStage()
    payload = json.dumps({"to": ["alice", "bob"], "dice": "d20"})
    env = MessageEnvelope(kind="DICE_REQUEST", payload_json=payload, origin_seq=4)

    assert stage.evaluate(envelope=env, view=_view(), player_id="alice").decision.include is True
    assert stage.evaluate(envelope=env, view=_view(), player_id="bob").decision.include is True
    assert stage.evaluate(envelope=env, view=_view(), player_id="carol").decision.include is False


def test_non_targeted_kind_has_no_to_field_invariant() -> None:
    stage = CoreInvariantStage()
    env = MessageEnvelope(kind="NARRATION", payload_json='{"text":"hi"}', origin_seq=5)
    outcome = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert outcome.terminal is False


SELF_AUTHORED_PAYLOAD = json.dumps({"author_player_id": "alice", "action": "jump"})


def test_self_authored_kind_echoes_to_author_only() -> None:
    stage = CoreInvariantStage()
    env = MessageEnvelope(kind="PLAYER_ACTION", payload_json=SELF_AUTHORED_PAYLOAD, origin_seq=6)

    out_alice = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert out_alice.terminal is True
    assert out_alice.decision.include is True

    out_bob = stage.evaluate(envelope=env, view=_view(), player_id="bob")
    assert out_bob.terminal is True
    assert out_bob.decision.include is False


def test_self_authored_missing_author_field_omits_for_all_non_gm() -> None:
    stage = CoreInvariantStage()
    env = MessageEnvelope(kind="DICE_THROW", payload_json='{"dice": "d20"}', origin_seq=7)
    outcome = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert outcome.terminal is True
    assert outcome.decision.include is False


def test_thinking_is_gm_only_never_routed_to_players() -> None:
    stage = CoreInvariantStage()
    env = MessageEnvelope(kind="THINKING", payload_json='{"thought":"hmm"}', origin_seq=8)
    outcome = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert outcome.terminal is True
    assert outcome.decision.include is False
