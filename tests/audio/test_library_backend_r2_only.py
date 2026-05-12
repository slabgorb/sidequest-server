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
    resolved_path = Path(resolved)
    assert resolved_path.name == "combat.ogg"
    assert str(resolved_path.relative_to(pack_dir.resolve())) == "audio/music/combat.ogg"


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
    resolved_path = Path(resolved)
    assert resolved_path.name == "exploration.ogg"
    assert str(resolved_path.relative_to(pack_dir.resolve())) == "audio/music/exploration.ogg"


def test_resolve_sfx_returns_path_even_when_file_missing_on_disk(
    tmp_path: Path,
) -> None:
    """Parallel to the music guard — same silent-fallback shape."""
    config, pack_dir = _r2_only_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)
    cue = AudioCue(lane=AudioLane.SFX, sfx_id="door_creak", intensity=0.7)

    resolved = backend.resolve(cue)

    assert resolved is not None
    resolved_path = Path(resolved)
    assert resolved_path.name == "door_creak.ogg"
    assert str(resolved_path.relative_to(pack_dir.resolve())) == "audio/sfx/door_creak.ogg"


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


# ---------------------------------------------------------------------------
# Playtest 2026-05-11 regression — post-genre-load URL-shape input.
# ---------------------------------------------------------------------------
#
# ``sidequest.genre.loader._resolve_audio_urls`` rewrites every relative
# path on ``AudioConfig`` to an absolute URL at load time
# (``https://cdn.slabgorb.com/genre_packs/<slug>/audio/...``). The
# LibraryBackend used to do ``(self._base_path / chosen).resolve()`` even
# when ``chosen`` was already an absolute URL — ``pathlib.Path`` then
# normalized ``//`` to ``/``, so the relative-to-base output was
# ``"https:/cdn.slabgorb.com/..."`` (single slash). That broke the
# ``_maybe_prefix`` startswith check, which prepended
# ``genre_packs/<slug>/`` and routed the result through ``resolve_asset_url``
# a SECOND time, producing the doubled URL
# ``https://cdn.slabgorb.com/genre_packs/caverns_and_claudes/https:/cdn.slabgorb.com/...``
# observed in the playtest.


def _post_load_pack(tmp_path: Path) -> tuple[AudioConfig, Path]:
    """Build an AudioConfig that mirrors the post-``_resolve_audio_urls``
    shape: every path is an absolute CDN URL, not a relative pack path."""
    pack_dir = tmp_path / "genre_packs" / "caverns_and_claudes"
    pack_dir.mkdir(parents=True)
    base = "https://cdn.slabgorb.com/genre_packs/caverns_and_claudes"
    config = AudioConfig(
        mood_tracks={
            "tension": [
                MoodTrack(path=f"{base}/audio/music/tension_4.ogg", title="Awareness", bpm=75),
            ],
        },
        sfx_library={"door_creak": [f"{base}/audio/sfx/door_creak.ogg"]},
        mixer=MixerConfig(music_volume=0.8, sfx_volume=0.9, crossfade_default_ms=400),
        themes=[
            AudioTheme(
                name="exploration",
                mood="exploration",
                base_prompt="caverns underfoot",
                variations=[
                    AudioVariation(type="ambient", path=f"{base}/audio/music/exploration.ogg"),
                ],
            ),
        ],
    )
    return config, pack_dir


def test_resolve_music_url_shape_input_returns_url_unchanged(tmp_path: Path) -> None:
    """``_resolve_music`` (themes branch) must pass absolute URLs through.

    Post ``_resolve_audio_urls`` the configured ``path`` is already a CDN
    URL. ``LibraryBackend`` must not feed it through ``self._base_path / ...``
    — Path normalization corrupts ``https://`` into ``https:/``.
    """
    config, pack_dir = _post_load_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)
    cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.EXPLORATION, intensity=0.4)

    resolved = backend.resolve(cue)

    assert resolved is not None
    assert (
        str(resolved)
        == "https://cdn.slabgorb.com/genre_packs/caverns_and_claudes/audio/music/exploration.ogg"
    ), (
        "URL-shaped chosen path was Path-normalized: pathlib collapses "
        "`https://` to `https:/`, which downstream `_maybe_prefix` fails "
        "to detect — producing the playtest 2026-05-11 doubled URL."
    )


def test_resolve_music_mood_tracks_url_shape_input_returns_url_unchanged(
    tmp_path: Path,
) -> None:
    """Parallel coverage for the ``mood_tracks`` branch (no themes match)."""
    config, pack_dir = _post_load_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)
    cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.TENSION, intensity=0.6)

    resolved = backend.resolve(cue)

    assert resolved is not None
    assert (
        str(resolved)
        == "https://cdn.slabgorb.com/genre_packs/caverns_and_claudes/audio/music/tension_4.ogg"
    )


def test_resolve_sfx_url_shape_input_returns_url_unchanged(tmp_path: Path) -> None:
    """SFX parallels the music branches — same corruption shape."""
    config, pack_dir = _post_load_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)
    cue = AudioCue(lane=AudioLane.SFX, sfx_id="door_creak", intensity=0.7)

    resolved = backend.resolve(cue)

    assert resolved is not None
    assert (
        str(resolved)
        == "https://cdn.slabgorb.com/genre_packs/caverns_and_claudes/audio/sfx/door_creak.ogg"
    )


def test_wiring_post_load_pack_does_not_double_prefix_through_build_audio_cue_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end wiring for the post-load shape — the playtest 2026-05-11
    doubled-URL regression.

    Production: ``_resolve_audio_urls`` runs at genre-load time, so the
    LibraryBackend receives URL-shaped paths. ``build_audio_cue_payload``
    must emit the URL exactly once — never doubled.
    """
    monkeypatch.delenv("SIDEQUEST_ASSET_BASE_URL", raising=False)

    config, pack_dir = _post_load_pack(tmp_path)
    backend = LibraryBackend(config, base_path=pack_dir)

    music_cue = AudioCue(lane=AudioLane.MUSIC, mood=MoodCategory.EXPLORATION, intensity=0.4)
    sfx_cue = AudioCue(lane=AudioLane.SFX, sfx_id="door_creak", intensity=0.7)

    payload = build_audio_cue_payload(
        [music_cue, sfx_cue],
        audio_backend=backend,
        genre_slug="caverns_and_claudes",
    )

    expected_music = (
        "https://cdn.slabgorb.com/genre_packs/caverns_and_claudes/audio/music/exploration.ogg"
    )
    expected_sfx = (
        "https://cdn.slabgorb.com/genre_packs/caverns_and_claudes/audio/sfx/door_creak.ogg"
    )
    assert payload.music_track == expected_music, (
        f"double-prefix regression — got {payload.music_track!r}, expected single-prefix "
        f"{expected_music!r}"
    )
    assert payload.sfx_triggers == [expected_sfx]
    # Strong negative assertion: the doubled-URL shape must never appear.
    assert "https:/cdn" not in (payload.music_track or "")
    assert "https:/cdn" not in payload.sfx_triggers[0]
    assert payload.music_track is not None and payload.music_track.count("https://") == 1
    assert payload.sfx_triggers[0].count("https://") == 1
