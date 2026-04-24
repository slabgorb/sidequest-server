"""ThemeRotator — cooldown shuffle with intensity-weighted track selection.

Story 20-1: Replaces random.choice() in LibraryBackend._resolve_music() with
intelligent track rotation that avoids repeats and weights by intensity.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Any

from sidequest.audio.models import MoodCategory

# Variation types grouped by intensity affinity
_QUIET_TYPES = {"ambient", "sparse"}
_LOUD_TYPES = {"full", "overture", "tension_build"}


class ThemeRotator:
    """Selects tracks with cooldown-based shuffle and intensity weighting."""

    def __init__(self, cooldown_tracks: int = 3) -> None:
        self.cooldown_tracks = cooldown_tracks
        self._history: deque[str] = deque(maxlen=max(cooldown_tracks, 1) if cooldown_tracks > 0 else 0)
        self._last_mood: MoodCategory | None = None
        self._resolution_trigger: bool = False

    def pick(
        self,
        candidates: list[Any],
        intensity: float,
        mood: MoodCategory | None,
    ) -> str | None:
        if not candidates:
            return None

        # Detect mood change
        if mood is not None and mood != self._last_mood:
            # Resolution trigger: combat → non-combat
            if self._last_mood == MoodCategory.COMBAT and mood != MoodCategory.COMBAT:
                self._resolution_trigger = True
            # Reset cooldown on mood change
            if self._last_mood is not None:
                self._history.clear()
            self._last_mood = mood
        elif mood is None and self._last_mood is not None:
            self._history.clear()
            self._last_mood = None

        # Build eligible pool (not on cooldown)
        paths = [self._get_path(c) for c in candidates]
        cooldown_set = set(self._history)
        eligible = [c for c, p in zip(candidates, paths, strict=False) if p not in cooldown_set]

        # If all on cooldown, reset and allow all
        if not eligible:
            self._history.clear()
            eligible = list(candidates)

        # Apply resolution trigger boost
        if self._resolution_trigger:
            self._resolution_trigger = False
            chosen = self._pick_with_resolution(eligible, intensity)
        else:
            chosen = self._pick_weighted(eligible, intensity)

        path = self._get_path(chosen)
        if self.cooldown_tracks > 0:
            self._history.append(path)
        return path

    def _pick_weighted(self, candidates: list[Any], intensity: float) -> Any:
        weights = [self._weight(c, intensity) for c in candidates]
        return random.choices(candidates, weights=weights, k=1)[0]

    def _pick_with_resolution(self, candidates: list[Any], intensity: float) -> Any:
        weights = []
        for c in candidates:
            vtype = self._get_variation_type(c)
            if vtype == "resolution":
                weights.append(15.0)
            else:
                weights.append(self._weight(c, intensity))
        return random.choices(candidates, weights=weights, k=1)[0]

    def _weight(self, candidate: Any, intensity: float) -> float:
        vtype = self._get_variation_type(candidate)
        if vtype is None:
            return 1.0

        intensity = max(0.0, min(1.0, intensity))  # clamp to valid range
        if vtype in _QUIET_TYPES:
            return 1.0 + 3.0 * (1.0 - intensity)
        if vtype in _LOUD_TYPES:
            return 1.0 + 3.0 * intensity
        # resolution and unknown types get neutral weight
        return 1.0

    @staticmethod
    def _get_path(candidate: Any) -> str:
        return candidate.path

    @staticmethod
    def _get_variation_type(candidate: Any) -> str | None:
        # Variation model exposes .type; fall back to .variation_type for compatibility
        return getattr(candidate, "variation_type", getattr(candidate, "type", None))
