"""Data models for the audio pipeline.

Story 5-1: AudioCue model + AudioConfig genre pack schema

AudioLane: 2 audio lanes (music, sfx).
MoodCategory: 8 mood categories for music selection.
AudioCue: Backend-agnostic audio request.
AudioResult: Audio output with file path, duration, timing.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AudioLane(StrEnum):
    """Audio pipeline lanes."""

    MUSIC = "music"
    SFX = "sfx"


class MoodCategory(StrEnum):
    """Mood categories for music selection."""

    EXPLORATION = "exploration"
    COMBAT = "combat"
    TENSION = "tension"
    TRIUMPH = "triumph"
    MYSTERY = "mystery"
    SORROW = "sorrow"
    TAVERN = "tavern"
    RITUAL = "ritual"
    SETTLEMENT = "settlement"
    RUINS = "ruins"
    REST = "rest"


class AudioCue(BaseModel):
    """Backend-agnostic audio request — what to play."""

    lane: AudioLane
    mood: str | None = None
    intensity: float = 0.5
    sfx_id: str | None = None
    subject: str = ""
    crossfade_ms: int = Field(default=3000, ge=0)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    priority: int = 0
    metadata: dict[str, Any] = {}


class AudioResult(BaseModel):
    """Audio output — where the file is and how long it took."""

    audio_path: Path
    duration_ms: int
    lane: AudioLane
    cue: AudioCue
    source: str
    generation_time_ms: int = 0
