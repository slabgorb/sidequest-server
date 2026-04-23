"""GenreRuleStage — applies genre-configured rules."""
from __future__ import annotations

import json
import textwrap

from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.genre_stage import GenreRuleStage
from sidequest.game.projection.rules import load_rules_from_yaml_str
from sidequest.game.projection.view import SessionGameStateView


def _stage(yaml_text: str) -> GenreRuleStage:
    return GenreRuleStage(load_rules_from_yaml_str(yaml_text))


def _view() -> SessionGameStateView:
    return SessionGameStateView(
        gm_player_id="gm",
        player_id_to_character={"alice": "alice_char", "bob": "bob_char"},
    )


def test_no_rules_passes_through() -> None:
    stage = _stage("rules: []")
    env = MessageEnvelope(kind="NARRATION", payload_json='{"text":"hi"}', origin_seq=1)
    decision = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert decision.include is True
    assert decision.payload_json == '{"text":"hi"}'


def test_target_only_omits_non_recipients() -> None:
    yaml = textwrap.dedent(
        """
        rules:
          - kind: NARRATION
            target_only:
              field: text
        """
    )
    stage = _stage(yaml)
    payload = json.dumps({"text": "alice"})
    env = MessageEnvelope(kind="NARRATION", payload_json=payload, origin_seq=2)

    out_alice = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert out_alice.include is True

    out_bob = stage.evaluate(envelope=env, view=_view(), player_id="bob")
    assert out_bob.include is False


def test_include_if_omits_when_predicate_false() -> None:
    yaml = textwrap.dedent(
        """
        rules:
          - kind: NARRATION
            include_if: is_gm()
        """
    )
    stage = _stage(yaml)
    env = MessageEnvelope(kind="NARRATION", payload_json='{"text":"hi"}', origin_seq=3)
    decision = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert decision.include is False


def test_redact_fields_masks_unless_predicate_holds() -> None:
    yaml = textwrap.dedent(
        """
        rules:
          - kind: NARRATION
            redact_fields:
              - field: text
                unless: is_gm()
                mask: "**"
        """
    )
    stage = _stage(yaml)
    env = MessageEnvelope(kind="NARRATION", payload_json='{"text":"secret"}', origin_seq=4)
    decision = stage.evaluate(envelope=env, view=_view(), player_id="alice")
    assert decision.include is True
    assert json.loads(decision.payload_json) == {"text": "**"}


def test_redact_fields_leaves_unmasked_when_predicate_holds() -> None:
    yaml = textwrap.dedent(
        """
        rules:
          - kind: NARRATION
            redact_fields:
              - field: text
                unless: is_gm()
                mask: "**"
        """
    )
    stage = _stage(yaml)
    env = MessageEnvelope(kind="NARRATION", payload_json='{"text":"secret"}', origin_seq=5)
    view = SessionGameStateView(gm_player_id="gm", player_id_to_character={"gm": "gm_char"})
    decision = stage.evaluate(envelope=env, view=view, player_id="gm")
    assert json.loads(decision.payload_json) == {"text": "secret"}
