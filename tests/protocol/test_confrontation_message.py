from __future__ import annotations

from sidequest.protocol.messages import ConfrontationMessage, ConfrontationPayload


def test_confrontation_message_roundtrip() -> None:
    payload = ConfrontationPayload(
        type="combat",
        label="Dungeon Combat",
        category="combat",
        actors=[{"name": "Rux", "role": "combatant", "per_actor_state": {}}],
        metric={"name": "hp", "current": 10, "starting": 10,
                "direction": "Descending", "threshold_low": 0,
                "threshold_high": None},
        beats=[{"id": "attack", "label": "Attack", "metric_delta": 2}],
        secondary_stats=None,
        genre_slug="caverns_and_claudes",
        mood="combat",
        active=True,
    )
    msg = ConfrontationMessage(payload=payload, player_id="")
    serialized = msg.model_dump(mode="json", by_alias=True)
    assert serialized["type"] == "CONFRONTATION"
    assert serialized["payload"]["active"] is True
    assert serialized["payload"]["beats"][0]["id"] == "attack"
    assert serialized["payload"]["type"] == "combat"
    assert serialized["payload"]["label"] == "Dungeon Combat"
    assert serialized["payload"]["category"] == "combat"
    assert serialized["payload"]["genre_slug"] == "caverns_and_claudes"
    assert serialized["payload"]["mood"] == "combat"
    assert serialized["payload"]["actors"][0]["name"] == "Rux"
    assert serialized["payload"]["metric"]["current"] == 10
    # Verify every UI-contract key is present on the wire (secondary_stats None is omitted).
    ui_keys = {"type", "label", "category", "actors", "metric", "beats",
               "genre_slug", "mood", "active"}
    assert set(serialized["payload"].keys()) == ui_keys


def test_confrontation_message_supports_active_false_clear() -> None:
    payload = ConfrontationPayload(
        type="combat", label="", category="", actors=[], metric={}, beats=[],
        secondary_stats=None, genre_slug="caverns_and_claudes",
        mood=None, active=False,
    )
    msg = ConfrontationMessage(payload=payload, player_id="")
    assert msg.payload.active is False


def test_confrontation_payload_accepts_clear_builder_output() -> None:
    """Regression: ConfrontationPayload must accept build_clear_confrontation_payload's dict.

    The clear-builder returns ``mood: None``; the payload must tolerate it.
    """
    from sidequest.server.dispatch.confrontation import (
        build_clear_confrontation_payload,
    )

    clear_dict = build_clear_confrontation_payload(
        encounter_type="combat", genre_slug="caverns_and_claudes",
    )
    # Must not raise pydantic ValidationError.
    payload = ConfrontationPayload(**clear_dict)
    assert payload.active is False
    assert payload.mood is None


def test_game_message_roundtrips_confrontation_variant() -> None:
    """The discriminated GameMessage union must include CONFRONTATION."""
    from sidequest.protocol.messages import GameMessage

    raw = {
        "type": "CONFRONTATION",
        "payload": {
            "type": "combat", "label": "Dungeon", "category": "combat",
            "actors": [], "metric": {}, "beats": [], "secondary_stats": None,
            "genre_slug": "cac", "mood": "combat", "active": True,
        },
        "player_id": "",
    }
    msg = GameMessage.model_validate(raw)
    assert isinstance(msg.root, ConfrontationMessage)
    assert msg.root.payload.active is True
