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


def test_confrontation_message_supports_active_false_clear() -> None:
    payload = ConfrontationPayload(
        type="combat", label="", category="", actors=[], metric={}, beats=[],
        secondary_stats=None, genre_slug="caverns_and_claudes",
        mood="", active=False,
    )
    msg = ConfrontationMessage(payload=payload, player_id="")
    assert msg.payload.active is False


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
