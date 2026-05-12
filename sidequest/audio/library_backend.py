"""LibraryBackend — resolves AudioCues to file paths in genre pack audio dirs.

Story 5-4: The DJ, not the radio. Picks tracks but does NOT do playback.
"""

from __future__ import annotations

import random
from pathlib import Path

from sidequest.audio.models import AudioCue, AudioLane, AudioResult
from sidequest.audio.protocol import AudioBackend
from sidequest.audio.rotator import ThemeRotator
from sidequest.genre.models import AudioConfig


def _materialize_chosen(base_path: Path, chosen: str) -> str:
    """Return the chosen library entry as a resource locator string.

    Post genre-pack load (``sidequest.genre.loader._resolve_audio_urls``),
    paths on ``AudioConfig`` are already absolute CDN/local URLs — pass
    those through unchanged. Pre-load fixtures (and tests that bypass the
    loader) carry relative paths; resolve those against ``base_path`` so
    callers get an absolute filesystem path.

    Returning the raw URL string for the eager-resolved case is mandatory
    because ``pathlib.Path("https://x/y")`` collapses ``//`` to ``/`` —
    the playtest 2026-05-11 doubled-URL regression came from feeding URLs
    through ``base / chosen``.
    """
    if chosen.startswith(("http://", "https://")):
        return chosen
    return str((base_path / chosen).resolve())


class LibraryBackend(AudioBackend):
    """Resolves AudioCues to file paths from a genre pack's audio directory."""

    def __init__(self, audio_config: AudioConfig, base_path: Path) -> None:
        self._config = audio_config
        self._base_path = base_path
        self._rotator = ThemeRotator()

    @property
    def name(self) -> str:
        return "library"

    @property
    def base_path(self) -> Path:
        return self._base_path

    def resolve(self, cue: AudioCue) -> str | None:
        """Map an AudioCue to a resource locator string, or None if unresolvable.

        The returned string is either an absolute URL (eager-resolved
        production shape) or an absolute filesystem path (pre-load
        fixture shape). Callers route URLs through unchanged and strip
        the base prefix from filesystem paths before the asset-URL seam.
        """
        if cue.lane == AudioLane.MUSIC:
            return self._resolve_music(cue)
        if cue.lane == AudioLane.SFX:
            return self._resolve_sfx(cue)
        return None

    def _resolve_music(self, cue: AudioCue) -> str | None:
        if cue.mood is None:
            return None

        # Check theme families first
        theme_variations = []
        for theme in self._config.themes:
            mood_val = cue.mood.value if hasattr(cue.mood, "value") else cue.mood
            if theme.mood == mood_val:
                theme_variations.extend(theme.variations)

        if theme_variations:
            chosen_path = self._rotator.pick(
                theme_variations,
                intensity=cue.intensity,
                mood=cue.mood,
            )
            if chosen_path is None:
                return None
            return _materialize_chosen(self._base_path, chosen_path)

        # Fall back to mood_tracks
        mood_val = cue.mood.value if hasattr(cue.mood, "value") else cue.mood
        tracks = self._config.mood_tracks.get(mood_val, [])
        if not tracks:
            return None
        chosen_path = self._rotator.pick(tracks, intensity=cue.intensity, mood=cue.mood)
        if chosen_path is None:
            return None
        return _materialize_chosen(self._base_path, chosen_path)

    def _resolve_sfx(self, cue: AudioCue) -> str | None:
        if cue.sfx_id is None:
            return None
        variants = self._config.sfx_library.get(cue.sfx_id, [])
        if not variants:
            return None
        chosen = random.choice(variants)
        return _materialize_chosen(self._base_path, chosen)

    def list_tracks(self, lane: str) -> list[Path]:
        """List available tracks for a lane, excluding missing files."""
        if lane == "music":
            paths = []
            for tracks in self._config.mood_tracks.values():
                for track in tracks:
                    p = (self._base_path / track.path).resolve()
                    if p.exists():
                        paths.append(p)
            return paths
        if lane == "sfx":
            paths = []
            for variants in self._config.sfx_library.values():
                for variant in variants:
                    p = (self._base_path / variant).resolve()
                    if p.exists():
                        paths.append(p)
            return paths
        return []

    async def play(self, cue: AudioCue) -> AudioResult:
        """Resolve cue and return an AudioResult. Raises FileNotFoundError if unresolvable.

        ``AudioResult.audio_path`` is typed ``Path`` — this method is the
        local-disk playback surface (test/CLI only post-ADR-095, when
        playback moved to the UI). For URL-shaped resolution results the
        Path coercion is lossy, so this method is not called from the
        production wire path.
        """
        resolved = self.resolve(cue)
        if resolved is None:
            raise FileNotFoundError(f"No audio file for cue: {cue}")
        return AudioResult(
            audio_path=Path(resolved),
            duration_ms=0,
            lane=cue.lane,
            cue=cue,
            source="library",
        )

    async def warm_up(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def supports_lane(self, lane: AudioLane) -> bool:
        return lane in (AudioLane.MUSIC, AudioLane.SFX)
