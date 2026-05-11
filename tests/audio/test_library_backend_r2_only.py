"""Regression: LibraryBackend must not gate resolve() on local file presence.

Playtest 2026-05-11 (caverns_and_claudes / caverns_sunden MP): the
``caverns_and_claudes`` audio pack ships ``*_input_params.json`` ACE-Step
source files on local disk but the actual ``.ogg`` audio assets live only
on R2. ``_resolve_music`` and ``_resolve_sfx`` short-circuited on
``Path.exists()`` against ``self._base_path / chosen_path``, returning
``None`` for every cue. ``build_audio_cue_payload`` then set
``music_track=None`` and the CDN-routing seam at ``audio_cue.py:61``
(``resolve_asset_url``) — explicitly authored to make R2-only assets
work — never got called. Net result: every AUDIO_CUE dispatched with
``music_track=None`` and the UI played zero audio for any user.

This regression test pins the fix: ``resolve()`` must return the
configured pack-relative path even when no local file exists, so the
downstream CDN routing in ``build_audio_cue_payload`` produces a usable
CDN URL.

Includes a wiring test that walks the full pipeline
(LibraryBackend → build_audio_cue_payload → AudioCuePayload) with an
R2-only pack and asserts the final wire payload carries a CDN URL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.audio.library_backend import LibraryBackend
from sidequest.audio.models import AudioCue, AudioLane, MoodCategory
from sidequest.genre.models.audio import (
    AudioConfig,
    AudioTheme,
    AudioVariation,
    MixerConfig,
    MoodTrack,
)
from sidequest.server.audio_cue import build_audio_cue_payload


def _r2_only_pack(tmp_path: Path) -> tuple[AudioConfig, Path]:
    """Build an AudioConfig whose declared tracks have NO matching files
    on local disk (the R2-only scenario)."""
    pack_dir = tmp_path / "genre_packs" / "caverns_and_claudes"
    pack_dir.mkdir(parents=True)
    # No .ogg files written — assets live on R2.
    config = AudioConfig(
        mood_tracks={
            "tension": [
                MoodTrack(path="audio/music/combat.ogg", title="Combat", bpm=140),
            ],
        },
        sfx_library={"door_creak": ["audio/sfx/door_creak.ogg"]},
        mixer=MixerConfig(music_volume=0.8, sfx_volume=0.9, crossfade_default_ms=400),
        themes=[
            AudioTheme(
                name="exploration",
                mood="exploration",
                base_prompt="caverns underfoot",
                variations=[
                    AudioVariation(type="ambient", path="audio/music/exploration.ogg"),
                ],
            ),
        ],
    )
    return config, pack_dir


def test_resolve_music_returns_path_even_when_file_missing_on_disk(
    tmp_path: Path,
) -> None:
    """``_resolve_music`` (mood_tracks branch) must not short-circuit on
    ``Path.exists()``. R2-hosted assets are never on local disk."""
    config, pack_dir = _r2_only_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)
    cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.TENSION, intensity=0.6)

    resolved = backend.resolve(cue)

    assert resolved is not None, (
        "R2-only mood_tracks entry returned None — silent fallback "
        "regression (playtest 2026-05-11)."
    )
    assert resolved.name == "combat.ogg"
    assert str(resolved.relative_to(pack_dir.resolve())) == "audio/music/combat.ogg"


def test_resolve_music_theme_branch_returns_path_even_when_file_missing(
    tmp_path: Path,
) -> None:
    """``_resolve_music`` (themes branch) must not short-circuit either —
    caverns_and_claudes uses the themes form, and that's the one that
    was failing in the live playtest."""
    config, pack_dir = _r2_only_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)
    cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.EXPLORATION, intensity=0.4)

    resolved = backend.resolve(cue)

    assert resolved is not None
    assert resolved.name == "exploration.ogg"
    assert str(resolved.relative_to(pack_dir.resolve())) == "audio/music/exploration.ogg"


def test_resolve_sfx_returns_path_even_when_file_missing_on_disk(
    tmp_path: Path,
) -> None:
    """Parallel to the music guard — same silent-fallback shape."""
    config, pack_dir = _r2_only_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)
    cue = AudioCue(lane=AudioLane.SFX, sfx_id="door_creak", intensity=0.7)

    resolved = backend.resolve(cue)

    assert resolved is not None
    assert resolved.name == "door_creak.ogg"
    assert str(resolved.relative_to(pack_dir.resolve())) == "audio/sfx/door_creak.ogg"


def test_resolve_returns_none_only_when_config_lacks_entry(tmp_path: Path) -> None:
    """The real-None case (genuine config gap) must still return None —
    we want loud failure when nothing is configured, just not silent
    failure when files are R2-only."""
    config = AudioConfig(
        mood_tracks={},
        sfx_library={},
        mixer=MixerConfig(music_volume=0.8, sfx_volume=0.9, crossfade_default_ms=400),
        themes=[],
    )
    backend = LibraryBackend(config, base_path=tmp_path)
    cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.TENSION, intensity=0.6)

    assert backend.resolve(cue) is None


@pytest.mark.parametrize(
    "asset_base_url_env,expected_prefix",
    [
        (None, "https://cdn.slabgorb.com/genre_packs/caverns_and_claudes/"),
        ("local", "/genre/caverns_and_claudes/"),
    ],
)
def test_wiring_r2_only_pack_through_build_audio_cue_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    asset_base_url_env: str | None,
    expected_prefix: str,
) -> None:
    """End-to-end wiring: R2-only pack → LibraryBackend → build_audio_cue_payload
    → AudioCuePayload carrying a CDN URL.

    This is the wiring test required by CLAUDE.md — proves the resolver
    fix actually flows through the production seam that the SM's ROOT
    CAUSE pointed at (``audio_cue.py:61`` ``resolve_asset_url``).
    """
    if asset_base_url_env is None:
        monkeypatch.delenv("SIDEQUEST_ASSET_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("SIDEQUEST_ASSET_BASE_URL", asset_base_url_env)

    config, pack_dir = _r2_only_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)

    music_cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.EXPLORATION, intensity=0.4)
    sfx_cue = AudioCue(lane=AudioLane.SFX, sfx_id="door_creak", intensity=0.7)

    payload = build_audio_cue_payload(
        [music_cue, sfx_cue],
        audio_backend=backend,
        genre_slug="caverns_and_claudes",
    )

    assert payload.mood == "exploration"
    assert payload.music_track is not None, (
        "music_track must be a CDN/local URL string, not None — "
        "this is the playtest 2026-05-11 silent-failure shape."
    )
    assert payload.music_track.startswith(expected_prefix)
    assert payload.music_track.endswith("audio/music/exploration.ogg")
    assert payload.sfx_triggers == [f"{expected_prefix}audio/sfx/door_creak.ogg"]
