"""Wiring test: turn-end outbound frames include AUDIO_CUE alongside
NARRATION when the genre pack has an audio backend configured.

Unlike test_audio_dispatch.py (which calls _maybe_dispatch_audio directly
on synthetic fixtures), this test constructs a real LibraryBackend atop
an on-disk pack layout and drives the dispatcher through the public
method. Proves the DJ resolves paths that actually exist on disk rather
than hallucinating filenames.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.audio.library_backend import LibraryBackend
from sidequest.genre.models.audio import (
    AudioConfig,
    MixerConfig,
    MoodTrack,
)
from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import AudioCueMessage
from sidequest.server.session_handler import (
    WebSocketSessionHandler,
    _SessionData,
)


@pytest.fixture
def audio_pack(tmp_path: Path) -> tuple[Path, AudioConfig]:
    audio = tmp_path / "audio"
    (audio / "music" / "tension").mkdir(parents=True)
    (audio / "sfx").mkdir(parents=True)
    (audio / "music" / "tension" / "a.ogg").touch()
    (audio / "sfx" / "door_creak.ogg").touch()
    cfg = AudioConfig(
        mood_tracks={
            "tension": [
                MoodTrack(
                    path="audio/music/tension/a.ogg",
                    title="Tension",
                    bpm=90,
                ),
            ],
        },
        sfx_library={"door_creak": ["audio/sfx/door_creak.ogg"]},
        mixer=MixerConfig(
            music_volume=1.0,
            sfx_volume=1.0,
            crossfade_default_ms=3000,
        ),
    )
    return tmp_path, cfg


def _build_session_data(pack_dir: Path, cfg: AudioConfig) -> _SessionData:
    sd = _SessionData.__new__(_SessionData)
    sd.audio_backend = LibraryBackend(cfg, base_path=pack_dir)
    sd.player_id = "p-1"
    sd.genre_slug = "fixture_genre"
    sd.world_slug = "fixture_world"
    sd.snapshot = MagicMock()
    sd.snapshot.turn_manager.interaction = 7
    return sd


def _narration_result(text: str) -> NarrationTurnResult:
    return NarrationTurnResult(
        narration=text,
        agent_name="test",
        agent_duration_ms=0,
        token_count_in=0,
        token_count_out=0,
        is_degraded=False,
        prompt_tier="delta",
    )


def test_turn_end_outbound_includes_audio_cue_after_narration(
    audio_pack: tuple[Path, AudioConfig],
) -> None:
    pack_dir, cfg = audio_pack
    sd = _build_session_data(pack_dir, cfg)
    handler = WebSocketSessionHandler.__new__(WebSocketSessionHandler)
    result = _narration_result(
        "A door creaks open behind you. Shadows flicker across the "
        "chamber as something ominous stirs in the darkness."
    )

    audio_msg = handler._maybe_dispatch_audio(sd, result)

    assert audio_msg is not None, (
        "Turn with tension + SFX keywords must produce AUDIO_CUE"
    )
    assert isinstance(audio_msg, AudioCueMessage)
    assert audio_msg.type == MessageType.AUDIO_CUE
    assert audio_msg.payload.mood == "tension"
    assert audio_msg.payload.music_track == "audio/music/tension/a.ogg"
    assert audio_msg.player_id == "p-1"

    # The resolved music track must actually exist on disk — proves the
    # DJ is library-backed, not hallucinating paths.
    assert audio_msg.payload.music_track is not None
    full = pack_dir / audio_msg.payload.music_track
    assert full.exists(), f"library resolved a non-existent path: {full}"


def test_turn_end_without_audio_backend_emits_no_audio_cue() -> None:
    """When the genre pack has no audio config, the turn ships NARRATION
    alone — no AUDIO_CUE frame."""
    sd = _SessionData.__new__(_SessionData)
    sd.audio_backend = None
    sd.player_id = "p-1"
    sd.snapshot = MagicMock()
    sd.snapshot.turn_manager.interaction = 2
    handler = WebSocketSessionHandler.__new__(WebSocketSessionHandler)
    result = _narration_result(
        "A door creaks open. Tension floods the chamber."
    )

    assert handler._maybe_dispatch_audio(sd, result) is None
