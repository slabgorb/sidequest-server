"""Tests for audio model types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.genre.models import AudioConfig, MixerConfig, MoodTrack


class TestMoodTrack:
    def test_default_energy(self) -> None:
        t = MoodTrack(path="audio/test.ogg", title="Test", bpm=120)
        assert t.energy == pytest.approx(0.5)

    def test_explicit_energy(self) -> None:
        t = MoodTrack(path="audio/test.ogg", title="Test", bpm=120, energy=0.8)
        assert t.energy == pytest.approx(0.8)

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            MoodTrack.model_validate({"path": "x", "title": "T", "bpm": 60, "bogus": True})


class TestMixerConfig:
    def test_default_voice_volume(self) -> None:
        m = MixerConfig(music_volume=0.8, sfx_volume=0.9, crossfade_default_ms=500)
        assert m.voice_volume == pytest.approx(1.0)

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            MixerConfig.model_validate(
                {
                    "music_volume": 0.8,
                    "sfx_volume": 0.9,
                    "crossfade_default_ms": 500,
                    "extra": True,
                }
            )

    def test_roundtrip(self) -> None:
        m = MixerConfig(music_volume=0.5, sfx_volume=0.7, crossfade_default_ms=1000)
        data = m.model_dump()
        m2 = MixerConfig.model_validate(data)
        assert m2.music_volume == pytest.approx(0.5)


class TestAudioConfig:
    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AudioConfig.model_validate(
                {
                    "mood_tracks": {},
                    "sfx_library": {},
                    "mixer": {"music_volume": 0.8, "sfx_volume": 0.9, "crossfade_default_ms": 500},
                    "unknown_section": True,
                }
            )
