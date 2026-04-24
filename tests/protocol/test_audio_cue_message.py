"""AudioCuePayload + AudioCueMessage — protocol wire shape."""

from __future__ import annotations

import json

from sidequest.protocol import GameMessage
from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import AudioCueMessage, AudioCuePayload


def test_audio_cue_payload_defaults() -> None:
    payload = AudioCuePayload()
    assert payload.mood is None
    assert payload.music_track is None
    assert payload.sfx_triggers == []


def test_audio_cue_message_serializes_with_type_discriminator() -> None:
    msg = AudioCueMessage(
        payload=AudioCuePayload(
            mood="tension",
            music_track="audio/music/tension/a.ogg",
            sfx_triggers=["audio/sfx/door_creak.ogg"],
        ),
        player_id="p-1",
    )
    wire = json.loads(msg.model_dump_json())
    assert wire["type"] == MessageType.AUDIO_CUE.value
    assert wire["payload"]["mood"] == "tension"
    assert wire["payload"]["music_track"] == "audio/music/tension/a.ogg"
    assert wire["payload"]["sfx_triggers"] == ["audio/sfx/door_creak.ogg"]
    assert wire["player_id"] == "p-1"


def test_audio_cue_round_trips_through_game_message_union() -> None:
    raw = {
        "type": "AUDIO_CUE",
        "payload": {
            "mood": "combat",
            "music_track": "audio/music/combat/charge.ogg",
            "sfx_triggers": [],
        },
        "player_id": "p-2",
    }
    parsed = GameMessage.model_validate(raw)
    assert parsed.type == MessageType.AUDIO_CUE
    assert parsed.payload.mood == "combat"
    assert parsed.payload.music_track == "audio/music/combat/charge.ogg"
    assert parsed.payload.sfx_triggers == []
    assert parsed.player_id == "p-2"
