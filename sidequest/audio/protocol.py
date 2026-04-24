"""Audio backend protocol and MusicDirectorDecision model.

Story 5-1: AudioCue model + AudioConfig genre pack schema

AudioBackend: Abstract base class for audio playback backends.
MusicDirectorDecision: Structured output from the Music Director agent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from sidequest.audio.models import AudioCue, AudioLane, AudioResult, MoodCategory


class MusicDirectorDecision(BaseModel):
    """Structured mood decision from the Music Director Claude agent."""

    mood: MoodCategory
    intensity: float
    transition: str = "crossfade"
    sfx_triggers: list[str] = []
    reasoning: str = ""


class AudioBackend(ABC):
    """Abstract base for audio playback backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier (e.g. 'library', 'musicgen')."""
        ...

    @abstractmethod
    async def play(self, cue: AudioCue) -> AudioResult:
        """Play audio for the given cue."""
        ...

    @abstractmethod
    async def warm_up(self) -> None:
        """Pre-load models, allocate resources. Called once at startup."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Release resources."""
        ...

    @abstractmethod
    def supports_lane(self, lane: AudioLane) -> bool:
        """Whether this backend can handle the given lane."""
        ...
