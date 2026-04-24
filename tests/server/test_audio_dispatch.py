"""Unit tests for SessionHandler audio dispatch plumbing.

Extended in Task 4 with _maybe_dispatch_audio coverage.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sidequest.audio.library_backend import LibraryBackend
from sidequest.server.session_handler import _SessionData


def test_session_data_has_audio_backend_field() -> None:
    names = {f.name for f in fields(_SessionData)}
    assert "audio_backend" in names, (
        "SessionHandler needs per-session audio_backend to keep "
        "ThemeRotator cooldown state across turns."
    )


@pytest.fixture
def fake_audio_pack_dir(tmp_path: Path) -> Path:
    """A minimal on-disk genre pack structure with one mood track and one SFX."""
    audio_dir = tmp_path / "audio"
    (audio_dir / "music" / "tension").mkdir(parents=True)
    (audio_dir / "sfx").mkdir(parents=True)
    (audio_dir / "music" / "tension" / "a.ogg").touch()
    (audio_dir / "sfx" / "door_creak.ogg").touch()
    return tmp_path


def _minimal_audio_config():
    """Build an AudioConfig instance with just enough to exercise resolve()."""
    from sidequest.genre.models.audio import (
        AudioConfig,
        MixerConfig,
        MoodTrack,
    )

    return AudioConfig(
        mood_tracks={
            "tension": [MoodTrack(path="audio/music/tension/a.ogg", title="Tension", bpm=90)],
        },
        sfx_library={"door_creak": ["audio/sfx/door_creak.ogg"]},
        mixer=MixerConfig(music_volume=1.0, sfx_volume=1.0, crossfade_default_ms=3000),
    )


def test_library_backend_resolves_mood_track_under_pack_dir(
    fake_audio_pack_dir: Path,
) -> None:
    from sidequest.audio.models import AudioCue, AudioLane, MoodCategory

    backend = LibraryBackend(_minimal_audio_config(), base_path=fake_audio_pack_dir)
    cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.TENSION, intensity=0.5)
    resolved = backend.resolve(cue)
    assert resolved is not None
    assert resolved == (fake_audio_pack_dir / "audio/music/tension/a.ogg").resolve()


def test_build_audio_backend_returns_library_backend_for_configured_pack(
    monkeypatch: pytest.MonkeyPatch,
    fake_audio_pack_dir: Path,
) -> None:
    from sidequest.genre.loader import GenreLoader
    from sidequest.server.session_handler import WebSocketSessionHandler

    monkeypatch.setattr(
        GenreLoader, "find", lambda self, code: fake_audio_pack_dir,
    )
    handler = WebSocketSessionHandler.__new__(WebSocketSessionHandler)
    pack = MagicMock()
    pack.audio = _minimal_audio_config()

    backend = handler._build_audio_backend("test_genre", pack)

    assert isinstance(backend, LibraryBackend)
    assert backend.base_path == fake_audio_pack_dir


def test_build_audio_backend_returns_none_when_config_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from sidequest.genre.loader import GenreLoader
    from sidequest.genre.models.audio import AudioConfig, MixerConfig
    from sidequest.server.session_handler import WebSocketSessionHandler

    monkeypatch.setattr(GenreLoader, "find", lambda self, code: tmp_path)
    handler = WebSocketSessionHandler.__new__(WebSocketSessionHandler)
    pack = MagicMock()
    pack.audio = AudioConfig(
        mixer=MixerConfig(music_volume=1.0, sfx_volume=1.0, crossfade_default_ms=3000),
    )

    backend = handler._build_audio_backend("empty", pack)

    assert backend is None
