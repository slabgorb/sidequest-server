"""End-to-end visibility wiring: projection.yaml -> ComposedFilter -> real view.

Reviewer finding for Task 1: the SessionGameStateView zones/hidden_characters
fields are useless if the predicate path can't reach them. This test drives
``ComposedFilter.project()`` with a ``SessionGameStateView`` produced by
the production ``views.build_game_state_view(handler)`` against
a ``visible_to(target)`` rule. Before the fix, ``player_id_to_character``
was empty, so ``view.character_of(player_id)`` returned ``None`` and
``_visible_to`` short-circuited to ``False`` before the new zone data was
ever consulted. After the fix, the predicate must route through the real
zone data.
"""
from __future__ import annotations

import json

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.projection.composed import ComposedFilter
from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.rules import load_rules_from_yaml_str
from sidequest.game.session import Npc
from sidequest.server import views


def _make_character(name: str, *, statuses: list[str] | None = None) -> Character:
    return Character(
        core=CreatureCore(
            name=name,
            description=f"{name}, test hero",
            personality="stoic",
            inventory=Inventory(),
            statuses=list(statuses or []),
        ),
        char_class="Fighter",
        race="Human",
        backstory=f"{name} wanders the integration suite.",
    )


def _make_npc(name: str, *, location: str | None = None, statuses: list[str] | None = None) -> Npc:
    return Npc(
        core=CreatureCore(
            name=name,
            description=f"NPC {name}",
            personality="mysterious",
            inventory=Inventory(),
            statuses=list(statuses or []),
        ),
        location=location,
    )


def _rules_redact_target_unless_visible():
    # A narration event reveals a ``target`` character. Unless the viewer
    # can see the target (visible_to), mask the target's name.
    return load_rules_from_yaml_str(
        """
rules:
  - kind: NARRATION
    redact_fields:
      - field: target
        unless: visible_to(target)
        mask: "**REDACTED**"
        """
    )


def test_visible_to_round_trip_co_located_viewer_sees_target(session_fixture) -> None:
    """Viewer + target share a zone and the viewer is mapped to a character -> no redact."""
    sd, handler = session_fixture
    sd.snapshot.characters.append(_make_character("Alice"))
    sd.snapshot.npcs.append(_make_npc("Barkeep", location="Main Hall"))

    view = views.build_game_state_view(handler)
    filt = ComposedFilter(rules=_rules_redact_target_unless_visible(), pack_slug="test")

    envelope = MessageEnvelope(
        kind="NARRATION",
        payload_json=json.dumps({"target": "Barkeep", "text": "You greet the barkeep."}),
        origin_seq=1,
    )
    decision = filt.project(envelope=envelope, view=view, player_id=sd.player_id)

    assert decision.include is True
    payload = json.loads(decision.payload_json)
    # The predicate path reached the zone data: both Alice and Barkeep are
    # in "Main Hall", so visible_to(target) returned True and the field
    # stayed unmasked.
    assert payload["target"] == "Barkeep", (
        "visible_to predicate failed to see zone data — player_id_to_character "
        "is likely not populated. view.character_of(player_id) must resolve "
        "to the player's Character.name before the predicate can run."
    )


def test_visible_to_round_trip_different_zone_redacts(session_fixture) -> None:
    """Viewer is mapped but target is in a different zone -> field redacted."""
    sd, handler = session_fixture
    sd.snapshot.characters.append(_make_character("Alice"))
    sd.snapshot.npcs.append(_make_npc("Barkeep", location="The Tavern"))

    view = views.build_game_state_view(handler)
    filt = ComposedFilter(rules=_rules_redact_target_unless_visible(), pack_slug="test")

    envelope = MessageEnvelope(
        kind="NARRATION",
        payload_json=json.dumps({"target": "Barkeep", "text": "???"}),
        origin_seq=2,
    )
    decision = filt.project(envelope=envelope, view=view, player_id=sd.player_id)

    assert decision.include is True
    payload = json.loads(decision.payload_json)
    assert payload["target"] == "**REDACTED**"


def test_visible_to_round_trip_unknown_player_redacts(session_fixture) -> None:
    """Unknown player_id -> character_of returns None -> redact (conservative)."""
    sd, handler = session_fixture
    sd.snapshot.characters.append(_make_character("Alice"))
    sd.snapshot.npcs.append(_make_npc("Barkeep", location="Main Hall"))

    view = views.build_game_state_view(handler)
    filt = ComposedFilter(rules=_rules_redact_target_unless_visible(), pack_slug="test")

    envelope = MessageEnvelope(
        kind="NARRATION",
        payload_json=json.dumps({"target": "Barkeep", "text": "..."}),
        origin_seq=3,
    )
    decision = filt.project(envelope=envelope, view=view, player_id="unknown-player")

    assert decision.include is True
    payload = json.loads(decision.payload_json)
    assert payload["target"] == "**REDACTED**"


def test_visible_to_round_trip_hidden_target_redacts(session_fixture) -> None:
    """Target has a stealth status -> visible_to is False -> field redacted."""
    sd, handler = session_fixture
    sd.snapshot.characters.append(_make_character("Alice"))
    sd.snapshot.npcs.append(
        _make_npc("ShadowThief", location="Main Hall", statuses=["invisible"]),
    )

    view = views.build_game_state_view(handler)
    filt = ComposedFilter(rules=_rules_redact_target_unless_visible(), pack_slug="test")

    envelope = MessageEnvelope(
        kind="NARRATION",
        payload_json=json.dumps({"target": "ShadowThief", "text": "a shadow flickers"}),
        origin_seq=4,
    )
    decision = filt.project(envelope=envelope, view=view, player_id=sd.player_id)

    assert decision.include is True
    payload = json.loads(decision.payload_json)
    assert payload["target"] == "**REDACTED**"
