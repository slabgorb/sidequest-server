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

    def resolve(self, cue: AudioCue) -> Path | None:
        """Map an AudioCue to an absolute file path, or None if unresolvable."""
        if cue.lane == AudioLane.MUSIC:
            return self._resolve_music(cue)
        if cue.lane == AudioLane.SFX:
            return self._resolve_sfx(cue)
        return None

    def _resolve_music(self, cue: AudioCue) -> Path | None:
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
            return (self._base_path / chosen_path).resolve()

        # Fall back to mood_tracks
        mood_val = cue.mood.value if hasattr(cue.mood, "value") else cue.mood
        tracks = self._config.mood_tracks.get(mood_val, [])
        if not tracks:
            return None
        chosen_path = self._rotator.pick(tracks, intensity=cue.intensity, mood=cue.mood)
        if chosen_path is None:
            return None
        return (self._base_path / chosen_path).resolve()

    def _resolve_sfx(self, cue: AudioCue) -> Path | None:
        if cue.sfx_id is None:
            return None
        variants = self._config.sfx_library.get(cue.sfx_id, [])
        if not variants:
            return None
        chosen = random.choice(variants)
        return (self._base_path / chosen).resolve()

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
        """Resolve cue and return an AudioResult. Raises FileNotFoundError if unresolvable."""
        path = self.resolve(cue)
        if path is None:
            raise FileNotFoundError(f"No audio file for cue: {cue}")
        return AudioResult(
            audio_path=path,
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
