"""Audio configuration types from audio.yaml and voice_presets.yaml.

Port of sidequest-genre/src/models/audio.rs.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MoodTrack(BaseModel):
    """A single music track."""

    model_config = {"extra": "forbid"}

    path: str
    title: str
    bpm: int
    energy: float = 0.5


class AudioEffect(BaseModel):
    """An audio effect in a processing chain."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    effect_type: str = Field(alias="type", serialization_alias="type")
    params: dict[str, float] = Field(default_factory=dict)


class CreatureVoicePreset(BaseModel):
    """Voice preset for a creature type."""

    model_config = {"extra": "forbid"}

    creature_type: str
    description: str
    pitch: float
    rate: float
    effects: list[AudioEffect] = Field(default_factory=list)


class MixerConfig(BaseModel):
    """Mixer volume configuration."""

    model_config = {"extra": "forbid"}

    music_volume: float
    sfx_volume: float
    voice_volume: float = 1.0
    crossfade_default_ms: int


class AudioVariation(BaseModel):
    """A single variation within an audio theme."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    variation_type: str = Field(alias="type", serialization_alias="type")
    path: str


class AudioTheme(BaseModel):
    """A themed music collection with variations."""

    model_config = {"extra": "forbid"}

    name: str
    mood: str
    base_prompt: str
    variations: list[AudioVariation] = Field(default_factory=list)


class AudioAiGeneration(BaseModel):
    """AI music generation configuration."""

    model_config = {"extra": "forbid"}

    enabled: bool = False
    model: str | None = None
    max_generation_time_s: int | None = None
    cache_generated: bool | None = None


class FactionTriggers(BaseModel):
    """Trigger conditions for a faction theme."""

    model_config = {"extra": "forbid"}

    location: bool = False
    npc_present: bool = False
    reputation_threshold: int | None = None


class FactionThemeDef(BaseModel):
    """A faction-specific music theme with trigger conditions."""

    model_config = {"extra": "forbid"}

    faction_id: str
    track: MoodTrack
    triggers: FactionTriggers


class AudioConfig(BaseModel):
    """Audio configuration for music, SFX, and voice."""

    model_config = {"extra": "forbid"}

    mood_tracks: dict[str, list[MoodTrack]] = Field(default_factory=dict)
    sfx_library: dict[str, list[str]] = Field(default_factory=dict)
    creature_voice_presets: dict[str, CreatureVoicePreset] = Field(default_factory=dict)
    mixer: MixerConfig
    themes: list[AudioTheme] = Field(default_factory=list)
    ai_generation: AudioAiGeneration | None = None
    mood_keywords: dict[str, list[str]] = Field(default_factory=dict)
    mixer_defaults: MixerConfig | None = None
    mood_aliases: dict[str, str] = Field(default_factory=dict)
    faction_themes: list[FactionThemeDef] = Field(default_factory=list)


class TrackVariation(str, Enum):
    """Typed track variation — cinematic score cue categories."""

    full = "full"
    overture = "overture"
    ambient = "ambient"
    sparse = "sparse"
    tension_build = "tension_build"
    resolution = "resolution"


class VoiceConfig(BaseModel):
    """A single TTS voice configuration."""

    model_config = {"extra": "forbid"}

    model: str
    pitch: float
    rate: float
    effects: list[AudioEffect] = Field(default_factory=list)


class VoicePresets(BaseModel):
    """TTS voice preset configuration."""

    model_config = {"extra": "forbid"}

    narrator: VoiceConfig
    characters: dict[str, VoiceConfig] = Field(default_factory=dict)
