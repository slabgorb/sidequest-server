"""Phase 1 GameMessage payloads and the GameMessage discriminated union.

Port of the payload structs and GameMessage enum from
sidequest-protocol/src/message.rs (Phase 1 subset — 12 message types).

Wire format: Rust uses #[serde(tag = "type")] with struct variants.
Each serialized message has "type" at the top level, plus "payload" and
"player_id" as sibling fields:

    {"type": "PLAYER_ACTION", "payload": {"action": "...", "aside": false}, "player_id": ""}

GameMessage is modelled as a pydantic discriminated union using Annotated +
Field(discriminator="type"). Each concrete message class carries a Literal[MessageType]
discriminator, a payload field, and a player_id field.

Payloads with deny_unknown_fields in Rust use model_config = {"extra": "forbid"} here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, RootModel

from sidequest.protocol.base import ProtocolBase
from sidequest.protocol.enums import MessageType, NarratorVerbosity, NarratorVocabulary
from sidequest.protocol.models import (
    CartographyMetadata,
    CreationChoice,
    ExploredLocation,
    FogBounds,
    Footnote,
    InitialState,
    PartyMember,
    RolledStat,
    StateDelta,
)
from sidequest.protocol.types import NonBlankString

# ---------------------------------------------------------------------------
# PlayerActionPayload
# ---------------------------------------------------------------------------


class PlayerActionPayload(ProtocolBase):
    """Player action payload.

    Port of sidequest_protocol::PlayerActionPayload.
    """

    action: NonBlankString
    """The action text the player typed. Non-blank."""
    aside: bool = False
    """True if this is an out-of-character aside."""


# ---------------------------------------------------------------------------
# NarrationPayload
# ---------------------------------------------------------------------------


class NarrationPayload(ProtocolBase):
    """Narration payload with optional state delta and structured footnotes.

    Port of sidequest_protocol::NarrationPayload.
    """

    text: NonBlankString
    """The narrative text from the AI. Non-blank."""
    state_delta: StateDelta | None = None
    """Optional state changes resulting from this narration."""
    footnotes: list[Footnote] = Field(default_factory=list)
    """Structured footnotes — new discoveries and callbacks to prior knowledge."""
    seq: int = 0
    """Event-log sequence number assigned when this narration was persisted (MP-03 Task 3).
    Clients use this value as last_seen_seq on reconnect to catch up on missed events."""


# ---------------------------------------------------------------------------
# NarrationEndPayload
# ---------------------------------------------------------------------------


class NarrationEndPayload(ProtocolBase):
    """Turn-completion payload, optionally carrying the final state delta.

    Port of sidequest_protocol::NarrationEndPayload.
    """

    state_delta: StateDelta | None = None
    """Optional state changes at end of narration."""


# ---------------------------------------------------------------------------
# ThinkingPayload
# ---------------------------------------------------------------------------


class ThinkingPayload(ProtocolBase):
    """Thinking indicator (empty payload — just shows spinner).

    Port of sidequest_protocol::ThinkingPayload.
    """


# ---------------------------------------------------------------------------
# SessionEventPayload
# ---------------------------------------------------------------------------


class SessionEventPayload(ProtocolBase):
    """Session lifecycle events.

    Port of sidequest_protocol::SessionEventPayload.
    """

    event: str
    """Event type: 'connect', 'connected', 'ready', 'theme_css'."""
    player_name: str | None = None
    """Player name (on connect)."""
    genre: str | None = None
    """Genre slug (on connect)."""
    world: str | None = None
    """World slug (on connect)."""
    has_character: bool | None = None
    """Whether player has a character (on connected)."""
    initial_state: InitialState | None = None
    """Initial game state (on ready)."""
    css: str | None = None
    """Genre CSS content (on theme_css event)."""
    narrator_verbosity: NarratorVerbosity | None = None
    """Narrator verbosity setting (story 14-3). Optional for backward compat."""
    narrator_vocabulary: NarratorVocabulary | None = None
    """Narrator vocabulary/complexity setting (story 14-4). Optional for backward compat."""
    image_cooldown_seconds: int | None = None
    """Image generation cooldown in seconds (story 14-6). Optional."""
    game_slug: str | None = None
    """Slug-based game identifier (MP-01 Task 4). When set, server looks up the
    game by slug instead of the legacy genre+world+player path."""
    last_seen_seq: int = 0
    """Last event-log sequence number the client has seen (MP-03 Task 3).
    Used on reconnect so the server can replay missed events."""


# ---------------------------------------------------------------------------
# CharacterCreationPayload
# ---------------------------------------------------------------------------


class CharacterCreationPayload(ProtocolBase):
    """Character creation flow payload.

    Port of sidequest_protocol::CharacterCreationPayload.
    """

    phase: str
    """Creation phase: 'scene', 'confirmation', 'complete'."""
    scene_index: int | None = None
    """Current scene index (1-based)."""
    total_scenes: int | None = None
    """Total number of scenes."""
    prompt: str | None = None
    """Prompt text for the player."""
    summary: str | None = None
    """Recap of previous choices."""
    message: str | None = None
    """Flavor text."""
    choices: list[CreationChoice] | None = None
    """Available choices."""
    allows_freeform: bool | None = None
    """Whether freeform text input is allowed."""
    input_type: str | None = None
    """Input type hint ('text', 'select', etc.)."""
    loading_text: str | None = None
    """Genre-aware loading text for the spinner between scenes."""
    character_preview: Any | None = None
    """Preview of the character being created."""
    rolled_stats: list[RolledStat] | None = None
    """Rolled ability scores in genre-defined order."""
    choice: str | None = None
    """Player's choice (client → server)."""
    character: Any | None = None
    """Completed character data."""
    action: str | None = None
    """Navigation action from client: 'back' or 'edit'."""
    target_step: int | None = None
    """Target scene index for 'edit' action (0-based)."""


# ---------------------------------------------------------------------------
# TurnStatusPayload
# ---------------------------------------------------------------------------


class TurnStatusPayload(ProtocolBase):
    """Turn/round tracking.

    Port of sidequest_protocol::TurnStatusPayload.
    """

    player_name: NonBlankString
    """Which player this turn status is about. Non-blank."""
    status: str
    """'active' = this player's turn, 'resolved' = turn complete."""
    state_delta: StateDelta | None = None
    """Optional state delta."""


# ---------------------------------------------------------------------------
# PartyStatusPayload
# ---------------------------------------------------------------------------


class PartyStatusPayload(ProtocolBase):
    """Full party snapshot.

    Port of sidequest_protocol::PartyStatusPayload.
    """

    members: list[PartyMember]
    """All party members."""


# ---------------------------------------------------------------------------
# MapUpdatePayload
# ---------------------------------------------------------------------------


class MapUpdatePayload(ProtocolBase):
    """Map update for the map overlay.

    Port of sidequest_protocol::MapUpdatePayload.
    """

    current_location: NonBlankString
    """Current player location. Non-blank."""
    region: NonBlankString
    """Current region name. Non-blank."""
    explored: list[ExploredLocation]
    """Explored locations."""
    fog_bounds: FogBounds | None = None
    """Fog of war bounds."""
    cartography: CartographyMetadata | None = None
    """Cartography metadata from genre pack."""


# ---------------------------------------------------------------------------
# ChapterMarkerPayload
# ---------------------------------------------------------------------------


class ChapterMarkerPayload(ProtocolBase):
    """Chapter/scene marker payload.

    Port of sidequest_protocol::ChapterMarkerPayload.
    """

    title: str | None = None
    """Chapter title."""
    location: str | None = None
    """Current location name."""


# ---------------------------------------------------------------------------
# ActionQueuePayload
# ---------------------------------------------------------------------------


class ActionQueuePayload(ProtocolBase):
    """Action queue payload.

    Port of sidequest_protocol::ActionQueuePayload.
    """

    actions: list[Any] = Field(default_factory=list)
    """Queued actions."""


# ---------------------------------------------------------------------------
# ImagePayload / RenderQueuedPayload — visual-scene dispatch
# ---------------------------------------------------------------------------


class ImagePayload(ProtocolBase):
    """Rendered image reply. Mirrors the TypeScript ``ImagePayload`` in
    ``sidequest-ui/src/types/payloads.ts``. The ``url`` is served by the
    server's ``/renders/*`` static mount; absolute filesystem paths are
    translated on the way out."""

    url: str
    alt: str | None = None
    description: str | None = None
    caption: str | None = None
    render_id: str | None = None
    tier: str | None = None
    width: int | None = None
    height: int | None = None
    handout: bool | None = None


class RenderQueuedPayload(ProtocolBase):
    """Dispatch acknowledgement emitted the moment the server fires a
    render request at the daemon. The UI's ``ImageBusProvider`` uses
    ``render_id`` to hold a placeholder card until the matching
    ``IMAGE`` message lands."""

    render_id: str


# ---------------------------------------------------------------------------
# AudioCuePayload — DJ cue dispatch (mood + SFX, no daemon round-trip)
# ---------------------------------------------------------------------------


class AudioCuePayload(ProtocolBase):
    """Audio cue emitted alongside NARRATION. Tells the UI's audio provider
    which mood to crossfade to (if any) and which SFX to trigger this turn.
    Music persists across turns client-side; a turn with no mood change
    simply has ``mood=None``."""

    mood: str | None = None
    """MoodCategory.value if the interpreter detected a mood change, else None.
    None explicitly means 'no change' — the UI keeps the current track."""

    music_track: str | None = None
    """Library-relative path for the music track LibraryBackend selected.
    ``None`` when mood is None."""

    sfx_triggers: list[str] = Field(default_factory=list)
    """Zero or more SFX track paths (library-relative) to fire on this turn."""


# ---------------------------------------------------------------------------
# ErrorPayload
# ---------------------------------------------------------------------------


class ErrorPayload(ProtocolBase):
    """Error payload.

    Port of sidequest_protocol::ErrorPayload.
    """

    message: NonBlankString
    """Human-readable error message. Non-blank."""
    reconnect_required: bool | None = None
    """When true, client must re-send SESSION_EVENT{connect} before retrying."""


# ---------------------------------------------------------------------------
# PlayerPresencePayload
# ---------------------------------------------------------------------------


class PlayerPresencePayload(ProtocolBase):
    """Multiplayer presence event payload (MP-02 Task 4).

    Emitted when a player connects to or disconnects from a room so other
    connected players can update their party display in real time.
    """

    player_id: str
    """The player whose connection state changed."""
    state: Literal["connected", "disconnected"]
    """Whether the player just connected or disconnected."""


# ---------------------------------------------------------------------------
# PlayerSeatPayload
# ---------------------------------------------------------------------------


class PlayerSeatPayload(ProtocolBase):
    """Player seat claim payload (MP-02 Task 5).

    Sent by a player to claim a character slot (e.g., "rux" in caverns_and_claudes).
    """

    character_slot: str
    """The character slot being claimed."""


# ---------------------------------------------------------------------------
# SeatConfirmedPayload
# ---------------------------------------------------------------------------


class SeatConfirmedPayload(ProtocolBase):
    """Seat confirmation broadcast payload (MP-02 Task 5).

    Broadcast to all players when a player claims a character slot.
    """

    player_id: str
    """The player who claimed the seat."""
    character_slot: str
    """The character slot that was claimed."""


# ---------------------------------------------------------------------------
# ConfrontationPayload
# ---------------------------------------------------------------------------


class ConfrontationPayload(ProtocolBase):
    """Payload for CONFRONTATION — drives the ConfrontationOverlay UI.

    Shape mirrors sidequest-ui/src/components/ConfrontationOverlay.tsx
    ``ConfrontationData`` (L42-58). ``active=False`` signals the overlay
    to unmount. Story 3.4.
    """

    type: str
    label: str
    category: str
    actors: list[dict[str, Any]] = Field(default_factory=list)
    metric: dict[str, Any] = Field(default_factory=dict)
    beats: list[dict[str, Any]] = Field(default_factory=list)
    secondary_stats: dict[str, Any] | None = None
    genre_slug: str
    mood: str | None = None
    active: bool = True


# ---------------------------------------------------------------------------
# GamePausedPayload / GameResumedPayload
# ---------------------------------------------------------------------------


class GamePausedPayload(ProtocolBase):
    """Payload for GAME_PAUSED messages (MP-02 Task 6).

    Emitted when the room detects that one or more seated players are absent.
    The narrator will not process PLAYER_ACTION until all seated players are
    present again.
    """

    waiting_for: list[str]
    """Player IDs of seated-but-absent players causing the pause."""


# ---------------------------------------------------------------------------
# GameMessage — discriminated union over Phase 1 messages
#
# Rust wire format: {"type": "PLAYER_ACTION", "payload": {...}, "player_id": ""}
# The Rust GameMessage uses #[serde(tag = "type")] with struct variants.
# Each variant serializes "type" + its named fields ("payload", "player_id")
# as siblings in the JSON object.
#
# We model each variant as a concrete BaseModel with:
#   - type: Literal[MessageType.VARIANT] = MessageType.VARIANT (discriminator)
#   - payload: <PayloadType>
#   - player_id: str = ""
#
# GameMessage is a RootModel wrapping the Annotated union for discrimination.
# ---------------------------------------------------------------------------


class PlayerActionMessage(ProtocolBase):
    """GameMessage::PlayerAction wire representation."""

    type: Literal[MessageType.PLAYER_ACTION] = MessageType.PLAYER_ACTION
    payload: PlayerActionPayload
    player_id: str = ""


class NarrationMessage(ProtocolBase):
    """GameMessage::Narration wire representation."""

    type: Literal[MessageType.NARRATION] = MessageType.NARRATION
    payload: NarrationPayload
    player_id: str = ""


class NarrationEndMessage(ProtocolBase):
    """GameMessage::NarrationEnd wire representation."""

    type: Literal[MessageType.NARRATION_END] = MessageType.NARRATION_END
    payload: NarrationEndPayload
    player_id: str = ""


class ThinkingMessage(ProtocolBase):
    """GameMessage::Thinking wire representation."""

    type: Literal[MessageType.THINKING] = MessageType.THINKING
    payload: ThinkingPayload
    player_id: str = ""


class SessionEventMessage(ProtocolBase):
    """GameMessage::SessionEvent wire representation."""

    type: Literal[MessageType.SESSION_EVENT] = MessageType.SESSION_EVENT
    payload: SessionEventPayload
    player_id: str = ""


class CharacterCreationMessage(ProtocolBase):
    """GameMessage::CharacterCreation wire representation."""

    type: Literal[MessageType.CHARACTER_CREATION] = MessageType.CHARACTER_CREATION
    payload: CharacterCreationPayload
    player_id: str = ""


class ConfrontationMessage(ProtocolBase):
    """GameMessage::Confrontation wire representation (story 3.4)."""

    type: Literal[MessageType.CONFRONTATION] = MessageType.CONFRONTATION
    payload: ConfrontationPayload
    player_id: str = ""


class TurnStatusMessage(ProtocolBase):
    """GameMessage::TurnStatus wire representation."""

    type: Literal[MessageType.TURN_STATUS] = MessageType.TURN_STATUS
    payload: TurnStatusPayload
    player_id: str = ""


class PartyStatusMessage(ProtocolBase):
    """GameMessage::PartyStatus wire representation."""

    type: Literal[MessageType.PARTY_STATUS] = MessageType.PARTY_STATUS
    payload: PartyStatusPayload
    player_id: str = ""


class MapUpdateMessage(ProtocolBase):
    """GameMessage::MapUpdate wire representation."""

    type: Literal[MessageType.MAP_UPDATE] = MessageType.MAP_UPDATE
    payload: MapUpdatePayload
    player_id: str = ""


class ChapterMarkerMessage(ProtocolBase):
    """GameMessage::ChapterMarker wire representation."""

    type: Literal[MessageType.CHAPTER_MARKER] = MessageType.CHAPTER_MARKER
    payload: ChapterMarkerPayload
    player_id: str = ""


class ActionQueueMessage(ProtocolBase):
    """GameMessage::ActionQueue wire representation."""

    type: Literal[MessageType.ACTION_QUEUE] = MessageType.ACTION_QUEUE
    payload: ActionQueuePayload
    player_id: str = ""


class ErrorMessage(ProtocolBase):
    """GameMessage::Error wire representation."""

    type: Literal[MessageType.ERROR] = MessageType.ERROR
    payload: ErrorPayload
    player_id: str = ""


class PlayerPresenceMessage(ProtocolBase):
    """GameMessage::PlayerPresence wire representation (MP-02 Task 4)."""

    type: Literal[MessageType.PLAYER_PRESENCE] = MessageType.PLAYER_PRESENCE
    payload: PlayerPresencePayload
    player_id: str = ""


class PlayerSeatMessage(ProtocolBase):
    """GameMessage::PlayerSeat wire representation (MP-02 Task 5)."""

    type: Literal[MessageType.PLAYER_SEAT] = MessageType.PLAYER_SEAT
    payload: PlayerSeatPayload
    player_id: str = ""


class SeatConfirmedMessage(ProtocolBase):
    """GameMessage::SeatConfirmed wire representation (MP-02 Task 5)."""

    type: Literal[MessageType.SEAT_CONFIRMED] = MessageType.SEAT_CONFIRMED
    payload: SeatConfirmedPayload
    player_id: str = ""


class GamePausedMessage(ProtocolBase):
    """GameMessage::GamePaused wire representation (MP-02 Task 6).

    Broadcast when one or more seated players are absent. The narrator will not
    process PLAYER_ACTION until all seated players reconnect.
    """

    type: Literal[MessageType.GAME_PAUSED] = MessageType.GAME_PAUSED
    payload: GamePausedPayload
    player_id: str = ""


class ImageMessage(ProtocolBase):
    """GameMessage::Image wire representation — carries a rendered image URL."""

    type: Literal[MessageType.IMAGE] = MessageType.IMAGE
    payload: ImagePayload
    player_id: str = ""


class RenderQueuedMessage(ProtocolBase):
    """GameMessage::RenderQueued — fires the moment the server dispatches
    a render to the daemon, before the image is ready."""

    type: Literal[MessageType.RENDER_QUEUED] = MessageType.RENDER_QUEUED
    payload: RenderQueuedPayload
    player_id: str = ""


class AudioCueMessage(ProtocolBase):
    """GameMessage::AudioCue — DJ cue shipped with NARRATION."""

    type: Literal[MessageType.AUDIO_CUE] = MessageType.AUDIO_CUE
    payload: AudioCuePayload
    player_id: str = ""


class GameResumedMessage(ProtocolBase):
    """GameMessage::GameResumed wire representation (MP-02 Task 6).

    Broadcast when the last absent seated player reconnects and the room is no
    longer paused.
    """

    type: Literal[MessageType.GAME_RESUMED] = MessageType.GAME_RESUMED
    payload: dict = {}  # noqa: RUF012 — intentionally empty payload
    player_id: str = ""


# Discriminated union type alias for all Phase 1 variants.
_Phase1Variant = Annotated[
    PlayerActionMessage
    | NarrationMessage
    | NarrationEndMessage
    | ThinkingMessage
    | SessionEventMessage
    | CharacterCreationMessage
    | ConfrontationMessage
    | TurnStatusMessage
    | PartyStatusMessage
    | MapUpdateMessage
    | ChapterMarkerMessage
    | ActionQueueMessage
    | ErrorMessage
    | PlayerPresenceMessage
    | PlayerSeatMessage
    | SeatConfirmedMessage
    | GamePausedMessage
    | GameResumedMessage
    | ImageMessage
    | RenderQueuedMessage
    | AudioCueMessage,
    Field(discriminator="type"),
]


class GameMessage(RootModel[_Phase1Variant]):
    """Discriminated union of all Phase 1 GameMessage variants.

    Port of sidequest_protocol::GameMessage (#[serde(tag = "type")]).

    Wire format mirrors Rust exactly:
        {"type": "PLAYER_ACTION", "payload": {...}, "player_id": ""}

    Construct via GameMessage(root=PlayerActionMessage(...)) or parse via
    GameMessage.model_validate({...}).
    """

    @classmethod
    def parse_json(cls, data: str) -> GameMessage:
        """Parse a JSON string into a GameMessage."""
        return cls.model_validate_json(data)

    def to_json(self, **kwargs: Any) -> str:
        """Serialize to a JSON string."""
        return self.model_dump_json(**kwargs)

    @property
    def type(self) -> MessageType:
        """Return the message type discriminator."""
        return self.root.type  # type: ignore[return-value]

    @property
    def payload(self) -> Any:
        """Return the payload."""
        return self.root.payload  # type: ignore[union-attr]

    @property
    def player_id(self) -> str:
        """Return the player_id."""
        return self.root.player_id  # type: ignore[union-attr]
