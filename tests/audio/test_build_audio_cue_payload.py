"""build_audio_cue_payload — refactored to return AudioCuePayload."""

from __future__ import annotations

from pathlib import Path

from sidequest.audio.models import AudioCue, AudioLane, AudioResult, MoodCategory
from sidequest.audio.protocol import AudioBackend
from sidequest.protocol.messages import AudioCuePayload
from sidequest.server.audio_cue import build_audio_cue_payload


class _StubBackend(AudioBackend):
    """Minimal AudioBackend that returns canned resolved paths."""

    def __init__(self, base: Path, mapping: dict[tuple[str, str | None], Path]) -> None:
        self._base = base
        self._mapping = mapping

    @property
    def name(self) -> str:
        return "stub"

    @property
    def base_path(self) -> Path:
        return self._base

    def resolve(self, cue: AudioCue) -> Path | None:
        key = (cue.lane.value, cue.mood if cue.mood else cue.sfx_id)
        return self._mapping.get(key)

    async def play(self, cue: AudioCue) -> AudioResult:  # pragma: no cover — unused here
        raise NotImplementedError

    async def warm_up(self) -> None:  # pragma: no cover — unused here
        pass

    async def shutdown(self) -> None:  # pragma: no cover — unused here
        pass

    def supports_lane(self, lane: AudioLane) -> bool:  # pragma: no cover — unused here
        return True


def test_empty_cue_list_returns_empty_payload() -> None:
    payload = build_audio_cue_payload([])
    assert isinstance(payload, AudioCuePayload)
    assert payload.mood is None
    assert payload.music_track is None
    assert payload.sfx_triggers == []


def test_music_cue_without_backend_sets_mood_only() -> None:
    cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.TENSION, intensity=0.6)
    payload = build_audio_cue_payload([cue])
    assert payload.mood == "tension"
    assert payload.music_track is None
    assert payload.sfx_triggers == []


def test_music_cue_with_backend_resolves_relative_music_track(tmp_path: Path) -> None:
    resolved = tmp_path / "audio" / "music" / "tension" / "a.ogg"
    resolved.parent.mkdir(parents=True)
    resolved.touch()
    backend = _StubBackend(tmp_path, {("music", "tension"): resolved})
    cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.TENSION, intensity=0.6)

    payload = build_audio_cue_payload([cue], audio_backend=backend)

    assert payload.mood == "tension"
    assert payload.music_track == "audio/music/tension/a.ogg"


def test_sfx_cue_with_backend_rewrites_trigger_to_relative_path(tmp_path: Path) -> None:
    resolved = tmp_path / "audio" / "sfx" / "door_creak.ogg"
    resolved.parent.mkdir(parents=True)
    resolved.touch()
    backend = _StubBackend(tmp_path, {("sfx", "door_creak"): resolved})
    cue = AudioCue(lane=AudioLane.SFX, sfx_id="door_creak", intensity=0.7)

    payload = build_audio_cue_payload([cue], audio_backend=backend)

    assert payload.mood is None
    assert payload.music_track is None
    assert payload.sfx_triggers == ["audio/sfx/door_creak.ogg"]


def test_sfx_cue_without_backend_keeps_raw_sfx_id() -> None:
    cue = AudioCue(lane=AudioLane.SFX, sfx_id="door_creak", intensity=0.7)
    payload = build_audio_cue_payload([cue])
    assert payload.sfx_triggers == ["door_creak"]
