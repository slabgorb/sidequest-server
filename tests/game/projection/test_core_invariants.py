"""CoreInvariantStage — GM sees truth."""
from __future__ import annotations

from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.invariants import CoreInvariantStage, InvariantOutcome
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
