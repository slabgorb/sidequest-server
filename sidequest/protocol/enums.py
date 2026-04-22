"""Protocol enums: MessageType, NarratorVerbosity, NarratorVocabulary.

Port of the enum definitions from sidequest-protocol/src/message.rs.

MessageType is a Python str enum of all wire-format type strings that
appear in the serde rename attributes on GameMessage variants. It does
not exist as a standalone Rust enum — the wire strings are encoded as
#[serde(rename = "...")] on each GameMessage variant. The Python
protocol layer makes them explicit here for use in dispatch/routing.

NarratorVerbosity and NarratorVocabulary are direct ports of the Rust
enums from message.rs.
"""

from __future__ import annotations

from enum import Enum


class MessageType(str, Enum):
    """All WebSocket message type tags.

    Wire values match the serde rename strings on the Rust GameMessage enum.
    Use these constants when constructing or routing protocol messages.
    """

    PLAYER_ACTION = "PLAYER_ACTION"
    NARRATION = "NARRATION"
    NARRATION_END = "NARRATION_END"
    THINKING = "THINKING"
    SESSION_EVENT = "SESSION_EVENT"
    CHARACTER_CREATION = "CHARACTER_CREATION"
    TURN_STATUS = "TURN_STATUS"
    PARTY_STATUS = "PARTY_STATUS"
    MAP_UPDATE = "MAP_UPDATE"
    CONFRONTATION = "CONFRONTATION"
    RENDER_QUEUED = "RENDER_QUEUED"
    IMAGE = "IMAGE"
    AUDIO_CUE = "AUDIO_CUE"
    VOICE_SIGNAL = "VOICE_SIGNAL"
    VOICE_TEXT = "VOICE_TEXT"
    ACTION_QUEUE = "ACTION_QUEUE"
    CHAPTER_MARKER = "CHAPTER_MARKER"
    ERROR = "ERROR"
    ACTION_REVEAL = "ACTION_REVEAL"
    SCENARIO_EVENT = "SCENARIO_EVENT"
    ACHIEVEMENT_EARNED = "ACHIEVEMENT_EARNED"
    JOURNAL_REQUEST = "JOURNAL_REQUEST"
    JOURNAL_RESPONSE = "JOURNAL_RESPONSE"
    ITEM_DEPLETED = "ITEM_DEPLETED"
    RESOURCE_MIN_REACHED = "RESOURCE_MIN_REACHED"
    TACTICAL_STATE = "TACTICAL_STATE"
    TACTICAL_ACTION = "TACTICAL_ACTION"
    DICE_REQUEST = "DICE_REQUEST"
    DICE_THROW = "DICE_THROW"
    DICE_RESULT = "DICE_RESULT"
    BEAT_SELECTION = "BEAT_SELECTION"
    SCRAPBOOK_ENTRY = "SCRAPBOOK_ENTRY"
    PLAYER_PRESENCE = "PLAYER_PRESENCE"
    PLAYER_SEAT = "PLAYER_SEAT"
    SEAT_CONFIRMED = "SEAT_CONFIRMED"
    GAME_PAUSED = "GAME_PAUSED"
    GAME_RESUMED = "GAME_RESUMED"


class NarratorVerbosity(str, Enum):
    """Controls how verbose the narrator's prose output should be.

    Serializes as lowercase strings for wire compatibility with the React UI.
    Default is Standard. Solo sessions default to Verbose via
    default_for_player_count().
    """

    concise = "concise"
    """Keep descriptions to 1-2 sentences. Prioritize action over atmosphere."""
    standard = "standard"
    """Standard descriptive prose — balanced detail and pacing."""
    verbose = "verbose"
    """Elaborate with sensory details, world-building, and atmospheric prose."""

    @classmethod
    def default(cls) -> NarratorVerbosity:
        """Return the default verbosity (Standard)."""
        return cls.standard

    @classmethod
    def default_for_player_count(cls, player_count: int) -> NarratorVerbosity:
        """Return the default verbosity for a given player count.

        Solo sessions (1 player) default to Verbose for immersive storytelling.
        Multiplayer sessions (2+) default to Standard for pacing.
        """
        if player_count <= 1:
            return cls.verbose
        return cls.standard


class NarratorVocabulary(str, Enum):
    """Controls the prose complexity and diction of narrator output.

    Works alongside NarratorVerbosity (which controls length). Vocabulary
    controls word choice and sentence complexity. Serializes as lowercase
    strings for wire compatibility with the React UI. Default is Literary.
    """

    accessible = "accessible"
    """Simple, direct language. Approximately 8th-grade reading level."""
    literary = "literary"
    """Rich but clear prose. Varied vocabulary without being obscure."""
    epic = "epic"
    """Elevated, archaic, or mythic diction. Unrestricted complexity."""

    @classmethod
    def default(cls) -> NarratorVocabulary:
        """Return the default vocabulary (Literary)."""
        return cls.literary
