"""Per-connection WebSocket session lifecycle.

Port of sidequest-server/src/session.rs + the connect/playing dispatch in
dispatch/connect.rs and dispatch/mod.rs (Phase 1 narration path only).

State machine: AwaitingConnect → Creating → Playing.
- SESSION_EVENT{connect}: bind genre/world, load or create GameSnapshot, emit
  SESSION_EVENT{connected}.
- PLAYER_ACTION (in Playing state): sanitize → orchestrator → NARRATION +
  NarrationEnd + persist.
- Unsupported message in wrong state: emit ERROR, do not crash.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto
from hashlib import blake2b
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from sidequest.game.persistence import GameMode
    from sidequest.server.session_room import RoomRegistry, SessionRoom

from sidequest.agents.claude_client import ClaudeClient, LlmClient
from sidequest.agents.local_dm import LocalDM
from sidequest.agents.orchestrator import Orchestrator, TurnContext
from sidequest.agents.perception_rewriter import rewrite_for_recipient
from sidequest.audio.interpreter import AudioInterpreter
from sidequest.audio.library_backend import LibraryBackend
from sidequest.daemon_client import (
    DaemonClient,
    DaemonRequestError,
    DaemonUnavailableError,
    render_enabled,
)
from sidequest.game.archetype_apply import apply_archetype_resolved
from sidequest.game.builder import (
    BuilderError,
    CharacterBuilder,
)
from sidequest.game.character import Character
from sidequest.game.event_log import EventLog
from sidequest.game.lore_embedding import (
    embed_pending_fragments,
    retrieve_lore_context,
)
from sidequest.game.lore_seeding import seed_lore_from_char_creation
from sidequest.game.lore_store import LoreStore
from sidequest.game.persistence import (
    SaveSchemaIncompatibleError,
    SqliteStore,
    db_path_for_session,
)
from sidequest.game.projection.cache import ProjectionCache
from sidequest.game.projection.composed import ComposedFilter
from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.view import SessionGameStateView
from sidequest.game.projection_filter import FilterDecision, ProjectionFilter
from sidequest.game.region_init import RegionInitError, init_region_location
from sidequest.game.room_movement import (
    RoomGraphInitError,
    init_room_graph_location,
)
from sidequest.game.session import (
    GameSnapshot,
    NarrativeEntry,
)
from sidequest.game.status import Status
from sidequest.game.world_materialization import (
    CampaignMaturity,
    HistoryParseError,
    materialize_from_genre_pack,
)
from sidequest.genre.archetype.shim import resolve_archetype
from sidequest.genre.error import GenreValidationError
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS, GenreLoader
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.scenario import ScenarioPack
from sidequest.genre.models.world import NavigationMode
from sidequest.protocol import GameMessage, sanitize_player_text
from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import (
    AudioCueMessage,
    AudioCuePayload,
    ChapterMarkerMessage,
    ChapterMarkerPayload,
    CharacterCreationMessage,
    CharacterCreationPayload,
    ConfrontationMessage,
    ConfrontationPayload,
    GamePausedMessage,
    GamePausedPayload,
    GameResumedMessage,
    ImageMessage,
    ImagePayload,
    MapUpdateMessage,
    NarrationEndMessage,
    NarrationEndPayload,
    NarrationMessage,
    NarrationPayload,
    PartyStatusMessage,
    PartyStatusPayload,
    RenderQueuedMessage,
    RenderQueuedPayload,
    ScrapbookEntryMessage,
    ScrapbookEntryNpcRef,
    ScrapbookEntryPayload,
    SeatConfirmedMessage,
    SeatConfirmedPayload,
    SecretNoteMessage,
    SecretNotePayload,
    SessionEventMessage,
    SessionEventPayload,
    TurnStatusMessage,
    TurnStatusPayload,
)
from sidequest.protocol.models import (
    CharacterSheetDetails,
    Footnote,
    InventoryItem,
    InventoryPayload,
    PartyMember,
)
from sidequest.protocol.types import NonBlankString
from sidequest.server.audio_cue import build_audio_cue_payload
from sidequest.server.dispatch.chargen_loadout import apply_starting_loadout
from sidequest.server.dispatch.chargen_summary import render_confirmation_summary
from sidequest.server.dispatch.culture_context import resolve_culture_reference
from sidequest.server.dispatch.opening_hook import resolve_opening
from sidequest.server.dispatch.scenario_bind import bind_scenario
from sidequest.server.image_pacing import ImagePacingThrottle
from sidequest.telemetry.spans import (
    SPAN_ORCHESTRATOR_PROCESS_ACTION,  # noqa: F401 — re-exported for OTEL catalog consumers
    audio_backend_disabled_span,
    audio_backend_enabled_span,
    audio_dispatched_span,
    audio_skipped_span,
    orchestrator_process_action_span,
    turn_span,
)
from sidequest.telemetry.turn_record import PatchSummary, TurnRecord
from sidequest.telemetry.validator import Validator
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

logger = logging.getLogger(__name__)


def _hash_snapshot(snap: object) -> str:
    """BLAKE2b-16 fingerprint of a snapshot's repr. Used for before/after change detection."""
    return blake2b(repr(snap).encode(), digest_size=16).hexdigest()


tracer = trace.get_tracer("sidequest.server.session_handler")

# Stateless module-level AudioInterpreter; shared across all sessions.
# interpret() takes the AudioConfig as an argument, so the object
# carries no per-session state. See _maybe_dispatch_audio.
_AUDIO_INTERPRETER = AudioInterpreter()

# ---------------------------------------------------------------------------
# Event-kind → message class mapping (MP-03 Task 3)
# Extend this dict as additional kinds are routed through _emit_event.
# ---------------------------------------------------------------------------

_KIND_TO_MESSAGE_CLS: dict[str, type] = {
    "NARRATION": NarrationMessage,
    "CONFRONTATION": ConfrontationMessage,
    "SECRET_NOTE": SecretNoteMessage,
    "SCRAPBOOK_ENTRY": ScrapbookEntryMessage,
}

# Kinds persisted to the events table by side-channel writers (e.g.
# ``telemetry.watcher_hub._maybe_persist_encounter_row``) for OTEL replay
# but never fanned out to clients via ``_emit_event``. Replay must skip these
# rather than crash — pingpong 2026-04-26 [S3-BUG] Reconnect crash on
# ENCOUNTER_STARTED. Add new internal kinds here when their producers land.
_REPLAY_SKIP_KINDS: frozenset[str] = frozenset(
    {
        "ENCOUNTER_STARTED",
        "ENCOUNTER_BEAT_APPLIED",
        "ENCOUNTER_METRIC_ADVANCE",
        "ENCOUNTER_BEAT_SKIPPED",
        "ENCOUNTER_TAG_CREATED",
        "ENCOUNTER_STATUS_ADDED",
        "ENCOUNTER_YIELD",
        "ENCOUNTER_RESOLVED",
        "ENCOUNTER_RESOLUTION_SIGNAL",
    }
)


# ---------------------------------------------------------------------------
# Replay helper (MP-03 Task 4)
# Reconstructs a typed protocol message from a persisted EventRow on reconnect.
# Distinct from _emit_event (live fan-out) but reuses _KIND_TO_MESSAGE_CLS as
# the single source of truth for kind → message class mapping.
# ---------------------------------------------------------------------------

# Canonical 8-4-4-4-12 hex UUID pattern. Used to detect saves that wrote
# ``core.name`` as the opaque player UUID before the with_lobby_name fix
# landed. Anchored to reject partial matches (e.g. a UUID embedded inside
# a legitimate name like "Rux-116f74b2-...").
_UUID_HEX_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_uuid(value: str) -> bool:
    """True when ``value`` is shaped like a canonical UUID hex string.

    Case-insensitive, anchored at both ends. ``uuid.UUID(...)`` would be
    stricter (rejects weird variants), but for rename-on-resume we want
    the naive pattern match — if the saved name looks like a UUID, it's
    almost certainly the player_id that leaked in pre-fix.
    """
    return bool(_UUID_HEX_RE.match(value))


def _rename_resumed_character_if_uuid(
    *,
    snapshot: GameSnapshot,
    display_name: str,
    player_id: str,
) -> bool:
    """Rename characters whose ``core.name`` matches a UUID pattern.

    Walks ``snapshot.characters`` and, for each one whose ``core.name`` either
    equals the active ``player_id`` OR matches the canonical UUID regex,
    replaces the name with the ``display_name`` the client sent on connect.
    Returns True iff at least one character was renamed, so the caller can
    persist the snapshot. When ``display_name`` is blank or itself looks
    like a UUID we decline to rename — better to leave the UUID than to
    replace one opaque identifier with another.

    Context: pingpong 2026-04-24 — "Resumed character shows UUID as name".
    Pre-fix chargen used ``CharacterBuilder`` without ``with_lobby_name``,
    so the committed Character carried the player_id as its display name.
    The chargen path is fixed, but existing saves persist the UUID until
    the player is renamed. This function patches them on resume.

    Pydantic lets us assign through ``core.name``; the field_validator
    rejects blank strings, which is why we guard on ``display_name``.
    """
    if not display_name.strip():
        return False
    if _looks_like_uuid(display_name):
        return False
    renamed = False
    for character in snapshot.characters:
        current = character.core.name
        if current == player_id or _looks_like_uuid(current):
            character.core.name = display_name
            renamed = True
    return renamed


def _build_message_for_kind(*, kind: str, payload_json: str, seq: int) -> object | None:
    """Build a typed protocol message from a persisted event row for replay.

    Returns ``None`` for journal-only telemetry kinds (``_REPLAY_SKIP_KINDS``)
    so replay can step over them without crashing the entire reconnect — these
    rows live in the events table for OTEL persistence (see
    ``telemetry.watcher_hub._maybe_persist_encounter_row``) but were never
    intended for the client wire. Pingpong 2026-04-26 [S3-BUG]: dropping the
    fail-loud here is intentional; the caller logs the skip via OTEL.

    Raises ValueError for kinds that are neither in the live-emit map nor on
    the explicit skip-list — that's a real schema-drift bug worth surfacing.
    """
    import json

    if kind in _REPLAY_SKIP_KINDS:
        return None

    message_cls = _KIND_TO_MESSAGE_CLS.get(kind)
    if message_cls is None:
        raise ValueError(f"_build_message_for_kind: unknown event kind {kind!r}")

    data = json.loads(payload_json)
    data["seq"] = seq

    if kind == "NARRATION":
        from sidequest.protocol.messages import NarrationPayload as _NarrationPayload

        return message_cls(payload=_NarrationPayload(**data))

    if kind == "CONFRONTATION":
        return message_cls(payload=ConfrontationPayload(**data))

    if kind == "SECRET_NOTE":
        return message_cls(payload=SecretNotePayload(**data))

    if kind == "SCRAPBOOK_ENTRY":
        return message_cls(payload=ScrapbookEntryPayload(**data))

    # Unreachable: _KIND_TO_MESSAGE_CLS guard above catches unknowns.
    # Kept as a belt-and-suspenders hard fail.
    raise ValueError(f"_build_message_for_kind: no payload constructor for kind {kind!r}")


# ---------------------------------------------------------------------------
# Per-turn write-split: canonical save + per-peer filtered frames (G8)
# ---------------------------------------------------------------------------
#
# MP spec 2026-04-22: the canonical save on the narrator-host holds the union
# of every event as appended to EventLog (unfiltered). Each peer save holds
# only the per-peer filtered subset — the frames whose FilterDecision.include
# is True.
#
# `_project_frames` is the single shared core: given one envelope + filter +
# list of connected players, compute the per-recipient decisions. Both the
# production turn driver (`_emit_event`) and the test-facing helper
# `apply_turn_writes_for_test` route through this function so the invariant
# is tested in the same code path production exercises.


@dataclass
class SentFrame:
    """One outbound frame to one peer after projection filter."""

    player_id: str
    payload_json: str


def _project_frames(
    *,
    envelope: MessageEnvelope,
    projection_filter: ProjectionFilter,
    connected_players: list[str],
    view: object = None,
    on_decision: Callable[[str, FilterDecision], None] | None = None,
) -> list[tuple[str, FilterDecision]]:
    """Run the projection filter once per connected player.

    Returns every (player_id, decision) pair — caller decides what to do with
    excluded decisions (e.g. production still writes them to the projection
    cache via ``on_decision`` before discarding the frame).

    The canonical EventLog append is the caller's responsibility; this helper
    is purely the filter fan-out step.
    """
    decisions: list[tuple[str, FilterDecision]] = []
    for pid in connected_players:
        decision = projection_filter.project(envelope=envelope, view=view, player_id=pid)
        if on_decision is not None:
            on_decision(pid, decision)
        decisions.append((pid, decision))
    return decisions


def apply_turn_writes_for_test(
    *,
    event_log: object,
    filter: ProjectionFilter,
    envelope: dict,
    connected_players: list[str],
    view: object = None,
) -> list[SentFrame]:
    """Test-facing write-split helper: canonical append + per-peer filter.

    Exercises the same core (`_project_frames`) as the production turn driver.
    The test fake ``event_log`` accepts a single positional MessageEnvelope on
    ``append``; production uses ``append_in_transaction(kind=..., payload_json=...)``
    inside a DB transaction — both converge on `_project_frames` for the
    per-peer decision loop.

    Canonical save receives the raw envelope exactly once. Each peer frame is
    emitted only when ``FilterDecision.include`` is True.
    """
    import json as _json

    canonical_env = MessageEnvelope(
        kind=envelope["kind"],
        payload_json=_json.dumps(envelope["payload"]),
        origin_seq=getattr(event_log, "next_seq", 0),
    )
    event_log.append(canonical_env)  # type: ignore[attr-defined]

    decisions = _project_frames(
        envelope=canonical_env,
        projection_filter=filter,
        connected_players=connected_players,
        view=view,
    )
    return [
        SentFrame(player_id=pid, payload_json=decision.payload_json)
        for pid, decision in decisions
        if decision.include
    ]


# ---------------------------------------------------------------------------
# Session state machine
# ---------------------------------------------------------------------------


class _State(Enum):
    AwaitingConnect = auto()
    Creating = auto()
    Playing = auto()


@dataclass
class _SessionData:
    """Mutable session state once genre/world are bound."""

    genre_slug: str
    world_slug: str
    player_name: str
    player_id: str
    snapshot: GameSnapshot
    store: SqliteStore
    genre_pack: GenrePack
    orchestrator: Orchestrator
    # Character builder is present only during the Creating state. Initialized
    # in _handle_connect when has_character=False; consumed (and discarded) by
    # _handle_character_creation's confirmation commit when the Character lands
    # on snapshot.characters.
    builder: CharacterBuilder | None = None
    # Opening-hook seed + directive (Story 2.3 Slice B). Resolved once at
    # connect time from pack/world.openings. Both consumed together by the
    # opening-turn bootstrap after chargen confirmation (Slice H): the seed
    # becomes the first player action, the directive is injected into the
    # narrator's Early zone on turn 0 only. ``None`` means the pack has no
    # opening-hook entries — the first turn runs without a directive.
    opening_seed: str | None = None
    opening_directive: str | None = None
    # Narrator world context (Story 41-11 — closes the Phase 2.2 IOU
    # ``Culture.chargen`` filter). Resolved once at connect time from
    # pack/world.cultures with lore-only cultures filtered out; injected
    # into the narrator prompt's Valley zone on every turn. ``None`` when
    # the pack declares no cultures (empty reference string also
    # normalised to ``None`` so the zone section is skipped cleanly).
    # Phase 3 will extend this field to include setting + world-lore
    # blocks alongside the culture reference.
    world_context: str | None = None
    # Lore store (Story 2.3 Slice F). Per-session indexed knowledge
    # collection. Seeded at chargen confirmation from the builder's
    # scene choices so the narrator's RAG retrieval pipeline can see
    # the player's backstory decisions. Rust parity: Arc<Mutex<LoreStore>>
    # on app state — Python single-player keeps it on the session.
    lore_store: LoreStore = field(default_factory=LoreStore)
    # Audio DJ — per-session LibraryBackend so ThemeRotator cooldowns
    # persist across turns within a session. None when the genre pack
    # has no resolvable audio directory on disk (e.g. a pack defining
    # moods without a matching ``audio/`` subtree). See
    # _maybe_dispatch_audio for the dispatch path.
    audio_backend: LibraryBackend | None = None
    # Active scenario pack (Story 2.3 Slice D). Set at chargen
    # confirmation when the genre pack declares at least one scenario.
    # Rust parity: ``shared_session.active_scenario`` — lives on the
    # shared session in Rust's multi-player model; Python Phase 1 is
    # single-player so it lands on the connection-scoped state. Later
    # slices consume this for pressure events, scene-budget gating,
    # and accusation UI.
    active_scenario: ScenarioPack | None = None
    # MP-01 Task 4: slug-based connect fields. Set when connecting via
    # game_slug rather than the legacy genre+world path.
    game_slug: str | None = None
    mode: GameMode | None = None
    # Lore embed worker lifecycle (Story 37-33 round-trip #4). A live
    # reference to the most recent background embed task so cleanup() can
    # cancel it before the SQLite store closes and so _dispatch_embed_worker
    # can skip dispatch while a previous worker is still running. Both
    # guards prevent the fire-and-forget task from writing to an orphaned
    # in-memory lore_store after disconnect and from racing a sibling worker
    # at the ``await client.embed()`` yield point on rapid successive turns.
    embed_task: asyncio.Task[None] | None = None
    # Group B Local DM decomposer (Task 10). One instance per session so
    # the decomposer can maintain a persistent Haiku sub-session across
    # turns. Constructed with a default factory so existing _SessionData
    # construction sites require no change.
    local_dm: LocalDM = field(default_factory=LocalDM)
    # Last dice roll outcome (story 34 — physics-is-the-roll). Stashed on
    # DICE_THROW resolution and read by the next narration turn's context
    # builder so the narrator knows whether the roll succeeded. Cleared by
    # the consuming turn (``take`` semantics). None when no roll is
    # pending. Rust parity: ``pending_roll_outcome`` on SharedSession.
    pending_roll_outcome: Any | None = None
    # Rolling character's name for the dice-replay turn — paired with
    # ``pending_roll_outcome``. Read by ``_apply_narration_result_to_snapshot``
    # so it can drop ONLY the rolling actor's beat from the narrator's
    # ``beat_selections`` (already applied via ``dispatch_dice_throw``)
    # while still applying opponent-side beat selections so the opponent
    # dial can advance. Playtest 2026-04-25 [P0] regression: dropping all
    # selections wholesale left the opponent dial inert and made combat
    # one-sided. Cleared together with ``pending_roll_outcome``.
    pending_roll_actor: str | None = None
    # ADR-050 — image pacing throttle. Per-session, time-based cooldown that
    # suppresses render dispatches faster than human absorption speed.
    # Default 30s solo / 60s MP; created at chargen confirmation once the
    # session ``mode`` is known. Defaults to a solo throttle so the field
    # is always non-None for legacy/test session-data construction sites
    # that don't set ``mode`` explicitly.
    # NOTE: per-process state. Multi-worker uvicorn would split the throttle
    # across workers; revisit with a shared backing store if we go there.
    image_pacing_throttle: ImagePacingThrottle = field(default_factory=ImagePacingThrottle.for_solo)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class WebSocketSessionHandler:
    """Per-connection session: state machine + dispatch.

    Created fresh per WebSocket connection by the /ws endpoint factory.
    Not shared across connections (Phase 1 is single-player).
    """

    def __init__(
        self,
        *,
        claude_client_factory: Callable[[], LlmClient] | None = None,
        genre_pack_search_paths: list[Path] | None = None,
        save_dir: Path,
        validator: Validator | None = None,
    ) -> None:
        self._client_factory: Callable[[], LlmClient] = (
            claude_client_factory if claude_client_factory is not None else ClaudeClient
        )
        self._search_paths: list[Path] = (
            genre_pack_search_paths
            if genre_pack_search_paths is not None
            else DEFAULT_GENRE_PACK_SEARCH_PATHS
        )
        self._save_dir = save_dir
        self._validator: Validator | None = validator
        self._state = _State.AwaitingConnect
        self._session_data: _SessionData | None = None
        # Room context fields — populated by attach_room_context() during the
        # WebSocket lifecycle (ws_endpoint). Absent here means the handler is
        # being driven outside that lifecycle (e.g. unit tests that exercise
        # non-slug-connect code paths). The slug-connect branch rejects this
        # loudly rather than silently skipping room wiring.
        self._room_registry: RoomRegistry | None = None
        self._socket_id: str | None = None
        self._out_queue: asyncio.Queue[object] | None = None
        self._room: SessionRoom | None = None
        # EventLog + projection filter are bound in the slug-connect branch.
        # The legacy genre/world connect path leaves them None; _emit_event
        # falls back to a plain message without seq in that case. This is a
        # real production code path (not a test-only skip), documented below.
        self._event_log: EventLog | None = None
        self._projection_filter: ProjectionFilter | None = None
        self._projection_cache: ProjectionCache | None = None

    # ------------------------------------------------------------------
    # Room context (MP-02 Task 2)
    # ------------------------------------------------------------------

    def attach_room_context(
        self,
        *,
        registry: RoomRegistry,
        socket_id: str,
        out_queue: asyncio.Queue[object],
    ) -> None:
        """Attach the process-wide RoomRegistry, socket_id, and per-socket outbound queue.

        Called by ws_endpoint immediately after accept(). _room is assigned in
        the slug-connect branch when a room is joined. out_queue is the
        asyncio.Queue that the writer task in ws_endpoint drains.

        All three fields are required. The slug-connect branch fails loudly if
        this method was not called — there is no silent test-only path.
        """
        self._room_registry = registry
        self._socket_id = socket_id
        self._out_queue = out_queue

    def current_room(self) -> SessionRoom | None:
        """Return the room this handler is currently registered in, or None."""
        return self._room

    # Status tokens the engine uses to mark a creature as non-visible for
    # projection purposes. Compared case-insensitively against
    # ``CreatureCore.statuses`` via **whole-token membership** — substring
    # matching produced false-positives like ``"unhidden"`` or
    # ``"hidden_buff_removed"`` silently masking characters. Genre packs
    # that mint stealth/invisibility mechanics must emit statuses that are
    # exactly one of these tokens (case-insensitive) for the projection
    # filter's ``visible_to()`` to mask the creature. Kept conservative:
    # a missing/unknown marker must never unmask a target.
    _HIDDEN_STATUS_TOKENS: frozenset[str] = frozenset(
        {
            "hidden",
            "invisible",
            "stealth",
            "concealed",
        }
    )

    @classmethod
    def _is_hidden_status_list(cls, statuses: list[Status]) -> bool:
        return any(s.text.lower() in cls._HIDDEN_STATUS_TOKENS for s in statuses)

    def _build_game_state_view(self) -> SessionGameStateView:
        """Read-only view of current session state for the projection filter.

        Zone + visibility state is populated from the live ``GameSnapshot``:
        all player-characters share the party-level ``snapshot.location``,
        and NPCs report their per-entity ``Npc.location``. Creatures whose
        ``statuses`` contain a stealth-like marker go into
        ``hidden_characters`` so ``visible_to()`` masks them even when
        co-located with the viewer. Per-item ownership is not yet tracked
        and stays at the conservative default.

        **GM identity wiring (C1, still partial):**

        - Solo sessions have no separate GM player by design; ``gm_player_id``
          is correctly ``None`` there. ``CoreInvariantStage`` never
          short-circuits on ``is_gm()`` for solo — which is the right
          behavior, because in solo play the single player is the only
          recipient and has no counterpart to be "GM" to.
        - Multiplayer sessions *should* name a GM player (e.g. the session
          creator or a designated seat) so that ``unless: is_gm()`` in
          ``projection.yaml`` can exempt them. That wiring lives downstream
          of MP-02 seating — ``SessionRoom`` does not yet carry a GM seat
          designation, so we still fall through to ``None`` for multiplayer
          with a logged warning. Genre packs that ship ``unless: is_gm()``
          rules today will mask the GM identically to a regular player
          (the safe direction: over-redact rather than leak).

        **Player-character mapping:** ``Character`` does not yet carry a
        ``player_id`` attribute, so the session's active player_id
        (``sd.player_id``) is mapped to the first entry in
        ``snapshot.characters`` — the single-player case this branch is
        authoritative for today. MP seat-assignment (sprint 2) will feed
        the multi-player case via ``SessionRoom``. When no character
        exists yet (pre-chargen) the mapping stays empty and predicates
        that depend on ``character_of()`` evaluate to ``False`` (the
        masked direction).
        """
        sd = self._session_data
        if sd is None:
            return SessionGameStateView(gm_player_id=None, player_id_to_character={})

        from sidequest.game.persistence import GameMode  # noqa: PLC0415 — break import cycle

        # Solo: no human GM. None is correct; CoreInvariantStage's
        # gm-sees-all branch never fires for the single player.
        gm_player_id: str | None = None
        if sd.mode is not None and sd.mode != GameMode.SOLO:
            # Multiplayer: GM seat assignment not yet plumbed through
            # SessionRoom. Log one warning per build so GM-panel users
            # can see that ``unless: is_gm()`` rules are currently
            # over-masking the GM in multiplayer sessions.
            if not getattr(self, "_gm_wiring_warned", False):
                logger.warning(
                    "projection.gm_identity_unwired slug=%s mode=%s — "
                    "multiplayer sessions do not yet carry a GM-seat "
                    "designation; `unless: is_gm()` rules will mask the "
                    "GM like any other player until MP-02 GM seating "
                    "lands.",
                    sd.game_slug,
                    sd.mode,
                )
                self._gm_wiring_warned = True

        snapshot = sd.snapshot

        # Player -> Character.name mapping. Solo / single-player sessions
        # today have exactly one character; that character belongs to the
        # session's active player_id. Without this mapping, the predicate
        # path (e.g. ``visible_to(target)``) receives
        # ``view.character_of(player_id) is None`` and short-circuits to
        # False before ever consulting zone data. Populated from the
        # existing session state — no new fields introduced.
        mapping: dict[str, str] = {}
        if snapshot.characters:
            mapping[sd.player_id] = snapshot.characters[0].core.name

        # Zone + hidden-character tracking from the live snapshot. Characters
        # share the party-level location today (no per-character zone split
        # in the engine yet); NPCs carry their own ``location``. Keys are
        # creature names — the same identity the rest of the projection
        # system uses when it refers to characters by ID. Single pass per
        # collection so character_zones and hidden_characters stay in sync.
        character_zones: dict[str, str] = {}
        hidden_characters: set[str] = set()
        party_zone = snapshot.location or None

        # One-shot OTEL breadcrumb: if we have player-characters but no
        # party zone, every co-located visible_to() collapses to False.
        # The direction is conservative-correct but invisible to the GM
        # panel — surface it once per session so rule authors can see why
        # their ``visible_to`` rules are masking everything.
        if (
            party_zone is None
            and snapshot.characters
            and not getattr(self, "_party_zone_absent_warned", False)
        ):
            logger.warning(
                "projection.party_zone_absent_with_characters slug=%s "
                "characters=%d — snapshot.location is empty while "
                "snapshot.characters is non-empty; visible_to() / "
                "in_same_zone() will mask every co-located target until "
                "a location is set (typically the first encounter).",
                sd.game_slug,
                len(snapshot.characters),
            )
            self._party_zone_absent_warned = True

        for ch in snapshot.characters:
            name = ch.core.name
            if party_zone is not None:
                character_zones[name] = party_zone
            if self._is_hidden_status_list(ch.core.statuses):
                hidden_characters.add(name)
        for npc in snapshot.npcs:
            name = npc.core.name
            if npc.location:
                character_zones[name] = npc.location
            if self._is_hidden_status_list(npc.core.statuses):
                hidden_characters.add(name)

        return SessionGameStateView(
            gm_player_id=gm_player_id,
            player_id_to_character=mapping,
            character_zones=character_zones,
            hidden_characters=hidden_characters,
        )

    def status_effects_by_player(self) -> dict[str, list[str]]:
        """Per-player status-effect tokens, for PerceptionRewriter.

        Reads the *existing* character-status map on the active
        ``GameSnapshot`` — no new state is introduced. Mirrors the
        player->character mapping used by :meth:`_build_game_state_view`:
        the session's active ``player_id`` is mapped to the first entry
        in ``snapshot.characters`` (single-player authoritative today;
        MP seat-assignment will feed the multi-player case via
        ``SessionRoom`` in a later sprint, at which point this accessor
        should fan out the same way).

        Returns ``dict[player_id, list[status_token]]``. An empty dict
        (no session, no snapshot, no characters) is safe: the rewriter
        treats missing entries as "no status effects".
        """
        sd = self._session_data
        if sd is None:
            return {}
        snapshot = sd.snapshot
        if not snapshot.characters:
            return {}
        # Mirror _build_game_state_view's mapping: active player_id ->
        # first character. Any connected non-active player_id gets []
        # until MP seat-assignment plumbs a real mapping.
        return {sd.player_id: [s.text for s in snapshot.characters[0].core.statuses]}

    # ------------------------------------------------------------------
    # Slug-resume narrative tail backfill (pingpong 2026-04-24)
    # ------------------------------------------------------------------

    def _backfill_last_narration_block(
        self,
        *,
        player_id: str,
    ) -> list[object]:
        """Fetch the most recent NARRATION (and its preceding CHAPTER_MARKER,
        if one was emitted without an intervening narration) from the event
        log and re-emit them as cached-projection messages — regardless of
        ``last_seen_seq``.

        Used to paint the narrative pane on a fresh-browser slug-resume
        where the normal replay would otherwise be empty because the
        client's persisted ``last_seen_seq`` already covers the tail.

        Returns the messages in emit order (CHAPTER_MARKER first if present,
        then NARRATION). Silently returns an empty list when no narration
        has been logged, when the cache has no include=True decision for
        the relevant event, or when the event log is unavailable. The
        caller is responsible for updating replay telemetry.
        """
        if self._event_log is None or self._projection_cache is None:
            return []
        store = self._event_log.store
        with store._conn:
            narration_row = store._conn.execute(
                "SELECT seq, kind, payload_json FROM events "
                "WHERE kind = 'NARRATION' "
                "ORDER BY seq DESC LIMIT 1",
            ).fetchone()
        if narration_row is None:
            return []
        narration_seq = int(narration_row[0])

        with store._conn:
            chapter_row = store._conn.execute(
                "SELECT seq, kind, payload_json FROM events "
                "WHERE kind = 'CHAPTER_MARKER' AND seq < ? "
                "  AND seq > COALESCE("
                "    (SELECT MAX(seq) FROM events "
                "     WHERE kind = 'NARRATION' AND seq < ?),"
                "    0"
                "  ) "
                "ORDER BY seq DESC LIMIT 1",
                (narration_seq, narration_seq),
            ).fetchone()

        def _cached_payload(seq: int) -> str | None:
            with store._conn:
                row = store._conn.execute(
                    "SELECT include, payload_json FROM projection_cache "
                    "WHERE player_id = ? AND event_seq = ?",
                    (player_id, seq),
                ).fetchone()
            if row is None or not bool(row[0]) or row[1] is None:
                return None
            return str(row[1])

        messages: list[object] = []
        if chapter_row is not None:
            chapter_seq = int(chapter_row[0])
            chapter_payload = _cached_payload(chapter_seq)
            if chapter_payload is not None:
                messages.append(
                    _build_message_for_kind(
                        kind="CHAPTER_MARKER",
                        payload_json=chapter_payload,
                        seq=chapter_seq,
                    )
                )

        narration_payload = _cached_payload(narration_seq)
        if narration_payload is None:
            return messages  # Can't emit bare chapter without its narration
        messages.append(
            _build_message_for_kind(
                kind="NARRATION",
                payload_json=narration_payload,
                seq=narration_seq,
            )
        )
        return messages

    # ------------------------------------------------------------------
    # EventLog fan-out helper (MP-03 Task 3)
    # ------------------------------------------------------------------

    def _emit_event(self, kind: str, payload_model: object) -> object:
        """Persist an event to the EventLog and fan-out to all connected players.

        Invariants (per Plan 03):
        1. EventLog.append fires BEFORE any socket send.
        2. Fan-out consults ProjectionFilter per recipient.
        3. The emitter (self) receives the raw, unfiltered event.

        Returns the outbound message object for the calling player (the emitter).
        Falls back to a plain message without seq when EventLog is unavailable
        (legacy non-slug connect path doesn't initialize _event_log).
        """
        import json

        from pydantic import BaseModel

        message_cls = _KIND_TO_MESSAGE_CLS.get(kind)
        if message_cls is None:
            raise ValueError(f"_emit_event: unknown kind {kind!r}")

        event_log = self._event_log
        projection_filter = self._projection_filter

        # Serialize payload excluding seq (seq is assigned from the DB row)
        if isinstance(payload_model, BaseModel):
            payload_json = payload_model.model_dump_json(exclude={"seq"})
        else:
            payload_json = json.dumps(payload_model)  # type: ignore[arg-type]

        if event_log is not None:
            room = self._room
            emitter_player_id = self._session_data.player_id if self._session_data else None

            # C2: event append + all cache writes share a single transaction.
            # Projections are computed inside the block so the cache row's
            # event_seq is the freshly-assigned one. If the server crashes
            # mid-block, sqlite rolls back both the event row and any partial
            # cache rows — either the event is fully persisted with its
            # projection cache, or not at all.
            store = event_log.store
            conn = store._conn
            fanout: list[tuple[str, FilterDecision, dict]] = []
            with conn:
                row = event_log.append_in_transaction(
                    kind=kind, payload_json=payload_json, conn=conn
                )
                seq = row.seq

                if room is not None and projection_filter is not None:
                    view = self._build_game_state_view()
                    envelope = MessageEnvelope(
                        kind=row.kind,
                        payload_json=row.payload_json,
                        origin_seq=row.seq,
                    )
                    # G6: status-effect perception overlay. Built once per
                    # event (not per recipient) — snapshot statuses don't
                    # change mid-fanout.
                    status_effects = self.status_effects_by_player()

                    # G8: route through the shared write-split helper so the
                    # per-peer filter loop is a single code path (test and
                    # production exercise `_project_frames`).
                    recipients = [
                        pid for pid in room.connected_player_ids() if pid != emitter_player_id
                    ]

                    def _cache_decision(pid: str, decision: FilterDecision) -> None:
                        if self._projection_cache is not None:
                            self._projection_cache.write_in_transaction(
                                event_seq=seq,
                                player_id=pid,
                                decision=decision,
                                conn=conn,
                            )

                    decisions = _project_frames(
                        envelope=envelope,
                        projection_filter=projection_filter,
                        connected_players=recipients,
                        view=view,
                        on_decision=_cache_decision,
                    )
                    for other_pid, decision in decisions:
                        filtered_data: dict = {}
                        if decision.include:
                            filtered_data = json.loads(decision.payload_json)
                            # G6: PerceptionRewriter — strip spans whose kind
                            # is incompatible with the recipient's effective
                            # fidelity (base fidelity + status effects like
                            # blinded/deafened). Runs on the already-filtered
                            # payload, before WS send. Deterministic only;
                            # LLM re-voicing is deferred to post-MP.
                            filtered_data = rewrite_for_recipient(
                                canonical_payload=filtered_data,
                                viewer_player_id=other_pid,
                                status_effects=status_effects,
                            )
                        fanout.append((other_pid, decision, filtered_data))

            # Build emitter's message with raw, unfiltered payload + seq
            # (Invariant 3). model_copy with scalar update is safe here —
            # only `seq` is being added, no existing field is being replaced
            # with a filtered value.
            if isinstance(payload_model, BaseModel):
                emitter_payload = payload_model.model_copy(update={"seq": seq})
            else:
                emitter_payload = payload_model  # type: ignore[assignment]
            out_to_self = message_cls(payload=emitter_payload)

            # Socket fan-out happens AFTER the DB transaction commits. A
            # crash between commit and send is recoverable via the cache on
            # reconnect; sending before commit would risk a client observing
            # an event that never hit disk.
            if room is not None:
                payload_cls = type(payload_model) if isinstance(payload_model, BaseModel) else None
                for other_pid, decision, filtered_data in fanout:
                    if not decision.include:
                        continue
                    socket_id = room.socket_for_player(other_pid)
                    if socket_id is None:
                        continue
                    queue = room.queue_for_socket(socket_id)
                    if queue is None:
                        continue
                    try:
                        if payload_cls is not None:
                            # C3: rebuild the recipient payload from the
                            # filtered dict alone (plus seq). Do NOT use
                            # model_copy(update=...) — merging leaves fields
                            # absent from the filtered dict at their canonical
                            # values, which would leak any field a future rule
                            # drops entirely.
                            recipient_payload = payload_cls.model_validate(
                                {**filtered_data, "seq": seq}
                            )
                            recipient_msg = message_cls(payload=recipient_payload)
                        else:
                            recipient_msg = message_cls(payload={**filtered_data, "seq": seq})
                    except Exception:
                        # Never silently fail fan-out; log and skip this recipient.
                        logger.error(
                            "emit_event.fanout_failed kind=%s other_pid=%s",
                            kind,
                            other_pid,
                        )
                        continue
                    queue.put_nowait(recipient_msg)
        else:
            # Legacy path (non-slug connect): no EventLog, no seq
            out_to_self = message_cls(payload=payload_model)

        return out_to_self

    # ------------------------------------------------------------------
    # Scrapbook entry emission (pingpong 2026-04-26 [S3-REGRESSION])
    # ------------------------------------------------------------------

    def _emit_scrapbook_entry(
        self,
        *,
        sd: _SessionData,
        snapshot: GameSnapshot,
        result: object,
    ) -> None:
        """Persist a scrapbook row + emit a SCRAPBOOK_ENTRY event for one turn.

        Called immediately after the NARRATION emit so the entry's seq lands
        adjacent to its narration in the journal. The IMAGE that may follow
        from the daemon is async — its URL arrives later and the UI gallery
        merges by ``turn_id``. We never block on the daemon here.

        Pure reuse: location from snapshot, excerpt from the narrator's prose,
        NPCs from the orchestrator's structured extraction. No new LLM calls.
        """
        from sidequest.agents.orchestrator import NarrationTurnResult

        if not isinstance(result, NarrationTurnResult):
            return

        narration_text = (result.narration or "").strip()
        if not narration_text:
            # The UI requires a non-empty excerpt; skip cleanly when the turn
            # produced no prose (only happens in degraded edge cases).
            return

        # UI contract: ``location`` must be non-empty. Fall back to the raw
        # snapshot location when the display lookup yields nothing — better
        # to surface "Unknown" than to silently drop the entry.
        loc_display = _resolve_location_display(
            sd.genre_pack, sd.world_slug, snapshot.location
        ) or (snapshot.location or "Unknown")

        # Trim the excerpt to a reasonable length for caption rendering. The
        # narrator's full prose lives on the NarrationMessage; the scrapbook
        # caption is a short teaser.
        excerpt = narration_text
        if len(excerpt) > 320:
            excerpt = excerpt[:317].rstrip() + "..."

        # NPCs from the orchestrator's structured extraction — no new
        # inference. ``role`` is the side flag (player/opponent/neutral);
        # ``disposition`` falls back to role when no behavioral string was
        # extracted.
        npc_refs: list[ScrapbookEntryNpcRef] = []
        for mention in result.npcs_present or []:
            name = (getattr(mention, "name", "") or "").strip()
            if not name:
                continue
            role = getattr(mention, "side", "") or "neutral"
            disposition = getattr(mention, "role", "") or role
            npc_refs.append(
                ScrapbookEntryNpcRef(
                    name=name,
                    role=role,
                    disposition=disposition,
                )
            )

        # World facts: lift the narrator's footnote summaries when present.
        world_facts: list[str] = []
        for fn in result.footnotes or []:
            if not isinstance(fn, dict):
                continue
            summary = fn.get("summary") or fn.get("text") or ""
            if isinstance(summary, str) and summary.strip():
                world_facts.append(summary.strip())

        scene_type: str | None = None
        scene_title: str | None = None
        visual = getattr(result, "visual_scene", None)
        if visual is not None:
            tier = (getattr(visual, "tier", None) or "").strip()
            scene_type = tier or None
            subject = (getattr(visual, "subject", None) or "").strip()
            if subject:
                scene_title = subject[:120]

        turn_id = int(snapshot.turn_manager.interaction)

        payload = ScrapbookEntryPayload(
            turn_id=turn_id,
            location=loc_display,
            narrative_excerpt=excerpt,
            scene_title=scene_title,
            scene_type=scene_type,
            image_url=None,  # Async — IMAGE frame follows from the daemon
            world_facts=world_facts,
            npcs_present=npc_refs,
        )

        # Persist to the dedicated scrapbook_entries table — keeps the
        # gallery queryable post-game without walking the events journal.
        try:
            self._persist_scrapbook_entry(payload)
        except Exception as exc:  # noqa: BLE001 — persistence failure must not block emit
            logger.warning("scrapbook.persist_failed turn=%d error=%s", turn_id, exc)

        # Route through _emit_event so the journal gets a row + reconnect
        # replay surfaces prior entries to fresh sockets.
        self._emit_event("SCRAPBOOK_ENTRY", payload)

        # OTEL lie-detector: GM panel sees per-turn confirmation that the
        # scrapbook subsystem fired. Without this, regression #2 was
        # invisible for two stories.
        _watcher_publish(
            "state_transition",
            {
                "field": "scrapbook",
                "op": "entry_emitted",
                "turn_id": turn_id,
                "image_url": None,
                "location": loc_display,
                "npc_count": len(npc_refs),
                "world_fact_count": len(world_facts),
                "player_id": sd.player_id,
            },
            component="scrapbook",
        )

    # ------------------------------------------------------------------
    # MAP_UPDATE emission — slice 1 of N (pingpong 2026-04-26
    # [S3-PORT-REGRESSION]). The Rust port had three trigger sites for
    # MAP_UPDATE: every-turn refresh, location-change, and reconnect-replay.
    # Slice 1 ports only the cartography-render trigger so the UI's
    # MapOverlay stops receiving nothing the first time the narrator picks
    # the cartography tier. The other two trigger sites land in slice 2/3.
    # ------------------------------------------------------------------

    def _emit_map_update_for_cartography(
        self,
        *,
        sd: _SessionData,
        render_id: str,
        player_id: str,
    ) -> None:
        """Push a ``MAP_UPDATE`` frame to the player's outbound queue when a
        cartography render is dispatched. Mirrors the IMAGE async-emit
        pattern: direct queue push, no journaling, no fan-out via
        ``_emit_event``.

        Why no journaling: ``MAP_UPDATE`` is a derived view of world state —
        on reconnect the slice-3 reconnect-replay path will rebuild the
        current map from cartography + ``snapshot.discovered_regions``
        rather than replay every historical frame. Adding a journal arm
        would force a ``_KIND_TO_MESSAGE_CLS`` registration and a builder
        case in ``_build_message_for_kind``, which is out of scope for
        slice 1 (see hard scope cap in the story).

        OTEL: emits ``map.update_emitted`` so the GM panel's "lie detector"
        can confirm the map subsystem actually fired (CLAUDE.md OTEL
        Observability Principle). Without this event there is no way to
        distinguish "map rendered + UI updated" from "map rendered + UI
        showing stale state" — the exact failure mode the Rust impl had
        before its emit_map_update_telemetry helper landed.
        """
        from sidequest.server.dispatch.map_update import build_map_update_payload

        # Resolve the live outbound queue. Mirror of the IMAGE completion
        # path (story 37-30): when room context is bound, the registry's
        # current socket queue survives mid-turn reconnects; otherwise fall
        # back to the legacy out_queue captured at construction.
        target_queue: asyncio.Queue[object] | None = None
        room_slug: str | None = None
        if self._room is not None:
            room_slug = self._room.slug
            registry = self._room_registry
            if registry is not None:
                room = registry.get(room_slug)
                if room is not None:
                    socket_id = room.socket_for_player(player_id)
                    if socket_id is not None:
                        target_queue = room.queue_for_socket(socket_id)
        if target_queue is None:
            target_queue = self._out_queue
        if target_queue is None:
            logger.warning(
                "map_update.skipped reason=no_outbound_queue render_id=%s",
                render_id,
            )
            return

        # Pull cartography from the bound world. ``getattr`` chain handles
        # legacy/test fixtures where the world or its cartography may be
        # absent — emit anyway with cartography=None (the wire model allows
        # it) so the UI at least learns the current location.
        world = sd.genre_pack.worlds.get(sd.world_slug) if sd.genre_pack else None
        cartography = getattr(world, "cartography", None) if world is not None else None

        payload = build_map_update_payload(
            snapshot=sd.snapshot, cartography=cartography,
        )
        if payload is None:
            # No current location — emitting an empty MAP_UPDATE would make
            # the UI worse, not better. Surface via OTEL so the GM panel
            # can see the skip rather than silently dropping.
            _watcher_publish(
                "state_transition",
                {
                    "field": "map",
                    "op": "skipped",
                    "reason": "no_current_location",
                    "render_id": render_id,
                    "tier": "cartography",
                    "player_id": player_id,
                },
                component="map",
                severity="warning",
            )
            return

        msg = MapUpdateMessage(
            type=MessageType.MAP_UPDATE,  # type: ignore[arg-type]
            payload=payload,
            player_id=player_id,
        )

        try:
            target_queue.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning(
                "map_update.outbound_queue_full render_id=%s", render_id
            )
            return

        # OTEL lie-detector — every MAP_UPDATE that hits a queue gets a
        # span. Origin marker mirrors the Rust ``emit_map_update_telemetry``
        # helper so when the location-change and reconnect paths land in
        # slices 2/3, the GM panel can distinguish them at a glance.
        nav_mode = (
            payload.cartography.navigation_mode if payload.cartography else "none"
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "map",
                "op": "update_emitted",
                "origin": "cartography_render",
                "render_id": render_id,
                "tier": "cartography",
                "player_id": player_id,
                "room_slug": room_slug or "",
                "current_location": str(payload.current_location),
                "region": str(payload.region),
                "explored_count": len(payload.explored),
                "has_cartography": payload.cartography is not None,
                "cartography_navigation_mode": nav_mode,
                "genre": sd.genre_slug,
            },
            component="map",
        )
        logger.info(
            "map_update.emitted render_id=%s location=%s explored=%d",
            render_id,
            str(payload.current_location),
            len(payload.explored),
        )

    def _persist_scrapbook_entry(self, payload: ScrapbookEntryPayload) -> None:
        """Insert a scrapbook row into the dedicated table (schema in
        ``game/persistence.py``). The table allows multiple rows per turn —
        no UNIQUE on turn_id.
        """
        import json as _json

        if self._event_log is None:
            return  # Legacy non-slug path — no DB to write to
        store = self._event_log.store
        npcs_json = _json.dumps(
            [
                {"name": ref.name, "role": ref.role, "disposition": ref.disposition}
                for ref in payload.npcs_present
            ]
        )
        facts_json = _json.dumps(list(payload.world_facts))
        with store._conn:
            store._conn.execute(
                "INSERT INTO scrapbook_entries "
                "(turn_id, scene_title, scene_type, location, image_url, "
                " narrative_excerpt, world_facts, npcs_present) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload.turn_id,
                    payload.scene_title,
                    payload.scene_type,
                    payload.location,
                    payload.image_url,
                    payload.narrative_excerpt,
                    facts_json,
                    npcs_json,
                ),
            )

    # ------------------------------------------------------------------
    # Public entrypoints
    # ------------------------------------------------------------------

    @property
    def session_data(self) -> _SessionData | None:
        """Public read accessor for session state (used by tests and GM panel)."""
        return self._session_data

    async def handle_message(self, msg: GameMessage) -> list[object]:
        """Dispatch an inbound message; return list of outbound protocol message objects."""
        msg_type: str = msg.type  # type: ignore[attr-defined]

        if msg_type == "SESSION_EVENT":
            return await self._handle_session_event(msg)
        elif msg_type == "PLAYER_ACTION":
            return await self._handle_player_action(msg)
        elif msg_type == "CHARACTER_CREATION":
            return await self._handle_character_creation(msg)
        elif msg_type == "PLAYER_SEAT":
            return self._handle_player_seat(msg)
        elif msg_type == "DICE_THROW":
            return await self._handle_dice_throw(msg)
        elif msg_type == "YIELD":
            return self._handle_yield(msg)
        else:
            logger.warning(
                "session.unhandled_message_type type=%s state=%s",
                msg_type,
                self._state.name,
            )
            return [_error_msg(f"Unsupported message type in Phase 1: {msg_type}")]

    async def cleanup(self) -> None:
        """Called on disconnect — persist current state if in Playing."""
        if self._session_data is not None:
            # Cancel any in-flight embed worker first so it cannot write
            # to an orphaned in-memory lore_store after store.close().
            # CancelledError is a BaseException in Python 3.8+, so it
            # escapes every `except Exception` in the worker; we await
            # and swallow it here so disconnect never raises to the
            # WebSocket layer. Non-cancel exceptions (a real worker bug
            # that happened to surface during cancellation) are logged
            # at warning with exc_info — cleanup still proceeds.
            embed_task = self._session_data.embed_task
            if embed_task is not None and not embed_task.done():
                embed_task.cancel()
                try:
                    await embed_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 — cleanup must proceed
                    logger.warning(
                        "session.embed_task_cleanup_error type=%s error=%s",
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
            try:
                # ADR-037 Python port: room owns the canonical snapshot,
                # so a plain room.save() persists it once for every
                # session that disconnects. Legacy non-slug path falls
                # back to the per-session store.
                if self._room is not None:
                    self._room.save()
                else:
                    self._session_data.store.save(self._session_data.snapshot)
                logger.info(
                    "session.disconnect_save genre=%s world=%s player=%s "
                    "char_count=%d seat_count=%d",
                    self._session_data.genre_slug,
                    self._session_data.world_slug,
                    self._session_data.player_name,
                    len(self._session_data.snapshot.characters),
                    len(self._session_data.snapshot.player_seats),
                )
            except Exception as exc:
                logger.error("session.disconnect_save_failed error=%s", exc)
            finally:
                # ADR-037 Python port: when the session is bound to a room,
                # the room owns the SqliteStore lifecycle — every WS session
                # bound to the slug shares the same store reference, so
                # closing it from one session's cleanup() leaves
                # ``room.save()`` operating on a closed connection from any
                # other session's perspective and produces
                # ``session.disconnect_save_failed error=Cannot operate on
                # a closed database`` (playtest 2026-04-25 [BUG-LOW]). The
                # room's store is closed via ``room.close_store()`` at room
                # teardown — not from per-session cleanup.
                #
                # Legacy non-slug path (no room) still closes its
                # per-session store here — it is owned by the session.
                if self._room is None:
                    try:
                        self._session_data.store.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # PLAYER_SEAT dispatch (MP-02 Task 5)
    # ------------------------------------------------------------------

    def _handle_player_seat(self, msg: GameMessage) -> list[object]:
        """Handle a PLAYER_SEAT message (character slot claim).

        Seats the player in the room and broadcasts SEAT_CONFIRMED to all players.
        Returns empty list — the broadcast handles fan-out via the room.
        """
        from sidequest.telemetry.spans import mp_seat_span

        payload = msg.payload  # type: ignore[attr-defined]
        player_id = getattr(msg, "player_id", "") or (
            self._session_data.player_id if self._session_data else ""
        )
        character_slot = payload.character_slot

        slug_attr = self._room.slug if self._room is not None else ""
        with mp_seat_span(
            slug=slug_attr,
            player_id=player_id,
            character_slot=character_slot,
            room_bound=self._room is not None,
        ) as _seat_span:
            # Seat the player in the room (thread-safe, idempotent)
            if self._room is not None:
                self._room.seat(player_id, character_slot=character_slot)
                logger.info(
                    "session.player_seated player_id=%s character_slot=%s slug=%s",
                    player_id,
                    character_slot,
                    self._room.slug,
                )
                _seat_span.set_attribute("seated_count", len(self._room.seated_player_ids()))
            else:
                logger.warning(
                    "session.player_seat_no_room player_id=%s character_slot=%s",
                    player_id,
                    character_slot,
                )

            # Build and broadcast SEAT_CONFIRMED to all players
            confirmed_msg = SeatConfirmedMessage(
                payload=SeatConfirmedPayload(
                    player_id=player_id,
                    character_slot=character_slot,
                ),
            )

            if self._room is not None:
                self._room.broadcast(confirmed_msg, exclude_socket_id=None)

        return []

    # ------------------------------------------------------------------
    # DICE_THROW dispatch (story 34 port — restored for 2026-04-24 playtest)
    # ------------------------------------------------------------------

    async def _handle_dice_throw(self, msg: GameMessage) -> list[object]:
        """Resolve a DICE_THROW from the rolling player.

        The UI drives all rolls via confrontation beat selection: it builds
        the DiceRequest locally, auto-rolls in Rapier, and sends a single
        DICE_THROW carrying the beat_id + physics-settled faces. The server
        applies the beat, resolves the dice, broadcasts DiceRequest +
        DiceResult to the room, and then runs the narrator inline so the
        rolling player sees prose in the same round-trip.

        Returns [] — all outbound messages go through the room broadcast
        queue so every connected socket (rolling player included) sees the
        same event stream.
        """
        from sidequest.server.dispatch.dice import (
            DiceDispatchError,
            dispatch_dice_throw,
        )

        if self._state != _State.Playing:
            return [_error_msg("Cannot process DICE_THROW: not in Playing state")]
        if self._session_data is None:
            return [_error_msg("Internal error: session data missing")]

        sd = self._session_data
        payload = msg.payload  # type: ignore[attr-defined]
        rolling_player_id = getattr(msg, "player_id", "") or sd.player_id

        snapshot = sd.snapshot
        encounter = snapshot.encounter
        character = snapshot.characters[0] if snapshot.characters else None
        character_name = character.core.name if character is not None else "Unknown"
        stats: dict[str, int] = dict(character.stats) if character is not None else {}

        room_broadcast = None
        if self._room is not None:
            # Wrap the room's broadcast to a simple callable the dispatcher
            # can invoke without knowing about SessionRoom. exclude=None so
            # every connected socket (rolling + spectators) receives the
            # same DiceRequest + DiceResult stream.
            def _broadcast(m: object) -> None:
                assert self._room is not None  # captured under the guard above
                self._room.broadcast(m, exclude_socket_id=None)

            room_broadcast = _broadcast

        try:
            outcome = dispatch_dice_throw(
                payload=payload,
                rolling_player_id=rolling_player_id,
                character_name=character_name,
                character_stats=stats,
                encounter=encounter,
                pack=sd.genre_pack,
                session_id=f"{sd.genre_slug}:{sd.world_slug}:{sd.player_id}",
                round_number=snapshot.turn_manager.interaction,
                room_broadcast=room_broadcast,
            )
        except DiceDispatchError as exc:
            logger.warning("dice.dispatch_error error=%s", exc)
            return [_error_msg(f"Dice throw failed: {exc}")]

        # Persist the resolved outcome so follow-up narrator runs can use it
        # (Rust parity: pending_roll_outcome). Stashed on session_data for
        # the next turn's TurnContext to pick up if needed.
        sd.pending_roll_outcome = outcome.outcome
        sd.pending_roll_actor = character_name

        # Run the narrator inline with the synthesized beat-resolved action
        # so the rolling player sees prose in the same WebSocket round-trip.
        # Matches the Rust deferred-narrator intent end-to-end, collapsed to
        # a single server tick since Python's handler is sync w.r.t. the
        # read loop.
        lore_context = await self._retrieve_lore_for_turn(sd, outcome.replay_action_text)
        turn_context = _build_turn_context(sd, lore_context=lore_context, room=self._room)
        return await self._execute_narration_turn(
            sd,
            outcome.replay_action_text,
            turn_context,
        )

    # ------------------------------------------------------------------
    # YIELD dispatch (dual-track momentum Phase 3)
    # ------------------------------------------------------------------

    def _handle_yield(self, msg: GameMessage) -> list[object]:
        """Handle a YIELD message — player withdraws from the active encounter.

        Marks the actor withdrawn; resolves the encounter when every
        player-side actor has yielded or been taken out; refunds edge.
        Returns [] on success — encounter outcome fans out via the next
        narrator turn which reads and clears ``pending_resolution_signal``.
        """
        from sidequest.server.dispatch.yield_action import handle_yield

        if self._state != _State.Playing:
            return [_error_msg("Cannot process YIELD: not in Playing state")]
        if self._session_data is None:
            return [_error_msg("Internal error: session data missing")]

        sd = self._session_data
        player_id = getattr(msg, "player_id", "") or sd.player_id
        player_name = sd.player_name

        try:
            handle_yield(sd.snapshot, player_id=player_id, player_name=player_name)
        except ValueError as exc:
            return [_error_msg(str(exc))]

        return []

    # ------------------------------------------------------------------
    # SESSION_EVENT dispatch
    # ------------------------------------------------------------------

    async def _handle_session_event(self, msg: GameMessage) -> list[object]:
        payload: SessionEventPayload = msg.payload  # type: ignore[attr-defined]
        event = payload.event

        if event == "connect":
            return await self._handle_connect(payload, getattr(msg, "player_id", ""))
        else:
            logger.warning("session.unknown_event event=%s", event)
            return [_error_msg(f"Unknown SESSION_EVENT event: {event}")]

    async def _handle_connect(
        self,
        payload: SessionEventPayload,
        player_id: str,
    ) -> list[object]:
        # New slug-based path (preferred). Legacy genre+world path below remains for now.
        if getattr(payload, "game_slug", None):
            from sidequest.game.persistence import (
                GameMode,
                db_path_for_slug,
                get_game,
            )

            slug = payload.game_slug
            db = db_path_for_slug(self._save_dir, slug)
            if not db.exists():
                return [_error_msg(f"unknown game slug: {slug}")]
            store = SqliteStore(db)
            store.initialize()
            from sidequest.telemetry.watcher_hub import bind_event_store as _bind_event_store

            _bind_event_store(store)
            row = get_game(store, slug)
            if row is None:
                return [_error_msg(f"unknown game slug: {slug}")]
            if not player_id:
                player_id = str(uuid.uuid4())

            # Display name (playtest 2026-04-23 Bug 1). The UI stores the
            # player's chosen display name in localStorage and sends it on
            # the slug-connect envelope as ``payload.player_name``. Without
            # it the character-name fallback (``CharacterBuilder.with_
            # lobby_name``) falls back to the opaque player UUID — so
            # genre packs with no name-entry scene (mutant_wasteland etc.)
            # end up with a UUID string on the character sheet header.
            #
            # No silent fallback: if the client didn't send a name, log
            # loud and use the player_id as a last resort. The UI should
            # always send this field on the slug-connect payload — a
            # missing value is a protocol contract violation, not a
            # normal operating mode.
            display_name: str = (payload.player_name or "").strip()
            if not display_name:
                logger.warning(
                    "session.slug_connect.missing_player_name slug=%s player_id=%s "
                    "— falling back to player_id as display name. UI must send "
                    "payload.player_name on slug-connect envelopes.",
                    slug,
                    player_id,
                )
                display_name = player_id

            # Room registry wiring (MP-02 Task 2). attach_room_context must
            # have been called — slug-connect cannot proceed without a room
            # registry, socket id, and outbound queue. Fail loudly if the
            # WebSocket lifecycle was bypassed (no silent test-only path).
            if self._room_registry is None or self._socket_id is None or self._out_queue is None:
                raise RuntimeError(
                    "slug-connect requires attach_room_context() to have been "
                    "called first — WebSocket lifecycle wiring is missing. "
                    "Production: ws_endpoint calls it immediately after accept(). "
                    "Tests: construct a RoomRegistry and call attach_room_context."
                )
            from sidequest.server.session_room import SoloSlotConflict
            from sidequest.telemetry.spans import mp_slug_connect_span

            with mp_slug_connect_span(
                slug=slug,
                player_id=player_id,
                mode=str(row.mode.value) if hasattr(row.mode, "value") else str(row.mode),
            ) as _mp_span:
                room = self._room_registry.get_or_create(slug, mode=GameMode(row.mode))
                # Snapshot pause state BEFORE connecting so we can detect
                # whether this connect resolved an existing pause (MP-02 Task 6).
                was_paused_before_connect = room.is_paused()
                # Snapshot the peer set BEFORE we add the new player to
                # ``_connected``. This is the back-fill source for the
                # PLAYER_PRESENCE roster the new client needs (playtest
                # 2026-04-26 S2-BUG): the server already broadcasts
                # PLAYER_PRESENCE on each new connect (so later joiners show
                # up live for earlier joiners), but never sent the *new*
                # connection a snapshot of who was already there. Net effect
                # in a 4-player game: P1 sees all 4, P2 sees 3, P3 sees 2,
                # P4 sees only self. Excludes the connecting player_id in
                # case this is a same-player reconnect on a new socket.
                peers_to_backfill = [pid for pid in room.connected_player_ids() if pid != player_id]
                try:
                    room.connect(player_id, socket_id=self._socket_id)
                except SoloSlotConflict as exc:
                    _mp_span.set_attribute("solo_slot_conflict", True)
                    return [_error_msg(str(exc))]
                self._room = room
                room.attach_outbound(self._socket_id, self._out_queue)
                room.broadcast(
                    _presence_msg(player_id, "connected"),
                    exclude_socket_id=self._socket_id,
                )
                # PLAYER_PRESENCE back-fill (playtest 2026-04-26 S2-BUG).
                # Push one PRESENCE{connected} frame per pre-existing peer
                # into THIS socket's outbound queue. Existing peers' rosters
                # are already correct (the standard outbound broadcast above
                # handles them); only the connecting client needs catch-up.
                # Mirrors the live-join shape: the UI's PLAYER_PRESENCE
                # handler folds these straight into ``connectedPeerIds`` —
                # no UI change required (per OQ-2 analysis).
                for peer_id in peers_to_backfill:
                    self._out_queue.put_nowait(_presence_msg(peer_id, "connected"))
                if peers_to_backfill:
                    _watcher_publish(
                        "session_presence_backfill",
                        {
                            "slug": slug,
                            "player_id": player_id,
                            "backfilled_count": len(peers_to_backfill),
                            "backfilled_player_ids": peers_to_backfill,
                        },
                        component="session",
                        severity="info",
                    )
                    logger.info(
                        "session.presence_backfill slug=%s player_id=%s "
                        "backfilled_count=%d backfilled_player_ids=%s",
                        slug,
                        player_id,
                        len(peers_to_backfill),
                        peers_to_backfill,
                    )
                _mp_span.set_attribute("presence_backfill_count", len(peers_to_backfill))
                # If this connect resolved the pause (was paused before, not
                # paused now), broadcast GAME_RESUMED to all players
                # including the reconnecting socket (MP-02 Task 6).
                resolved_pause = was_paused_before_connect and not room.is_paused()
                if resolved_pause:
                    room.broadcast(
                        GameResumedMessage(),
                        exclude_socket_id=None,
                    )
                _mp_span.set_attribute("was_paused_before", was_paused_before_connect)
                _mp_span.set_attribute("resolved_pause", resolved_pause)
                _mp_span.set_attribute("connected_count", len(room.connected_player_ids()))

            # Load genre pack (Bug 1 fix: genre_pack must not be None).
            try:
                loader = GenreLoader(search_paths=self._search_paths)
                genre_pack = loader.load(row.genre_slug)
            except Exception as exc:
                logger.error(
                    "session.genre_load_failed genre=%s slug=%s error=%s",
                    row.genre_slug,
                    slug,
                    exc,
                )
                return [_error_msg(f"Failed to load genre pack '{row.genre_slug}': {exc}")]

            # Restore saved snapshot, or start fresh (Bug 2 fix: resume semantics).
            try:
                saved = store.load()
            except SaveSchemaIncompatibleError as exc:
                # Schema-incompatible save (e.g. legacy single-metric encounter
                # under dual-dial migration). Don't let pydantic's
                # ValidationError bubble up to websocket.py — that path closes
                # the socket without surfacing a reason and the UI sits in an
                # infinite reconnect loop. Return a typed ERROR so the UI can
                # render an actual error panel with escape actions.
                logger.warning(
                    "session.save_schema_invalid slug=%s path=%s error=%s",
                    slug,
                    exc.save_path,
                    exc.underlying,
                )
                _watcher_publish(
                    "save_schema_invalid",
                    {
                        "slug": slug,
                        "save_path": str(exc.save_path),
                        "validation_error": str(exc.underlying),
                    },
                    component="session",
                    severity="error",
                )
                return [
                    _error_msg(
                        f"This save predates the current schema and cannot be "
                        f"loaded. Start a new adventure or move the save aside: "
                        f"{exc.save_path}",
                        reconnect_required=False,
                        code="save_schema_invalid",
                    )
                ]
            if saved is not None:
                snapshot = saved.snapshot
                # Per-player chargen gate (playtest 2026-04-25). MP: a new
                # player_id joining a slug that already has a character must
                # route to chargen, not auto-claim the existing PC.
                #
                # Three branches:
                #   1. ``player_seats`` populated  → authoritative per-player
                #      binding; resume only if our player_id is seated.
                #   2. ``player_seats`` empty + SOLO → legacy single-PC resume;
                #      SoloSlotConflict already guarded the second connect, so
                #      ``has_character = bool(characters)`` is safe.
                #   3. ``player_seats`` empty + MP → playtest 2026-04-25 bug:
                #      Laverne's chargen completed on a pre-binding server,
                #      so the save has characters=[Laverne] but seats={}. The
                #      old fallback auto-claimed Laverne for ANY connecting
                #      player (Squiggy lands on Laverne's sheet labeled
                #      "(YOU)"). Fix: match by display_name. If display_name
                #      matches an existing character, this is the original
                #      player resuming — back-fill the seat. Otherwise it's
                #      a new joiner — route to chargen and emit a watcher
                #      event so the GM panel can see the joiner.
                _existing_char_names = {c.core.name for c in snapshot.characters if c.core.name}
                _is_mp = GameMode(row.mode) == GameMode.MULTIPLAYER
                if snapshot.player_seats:
                    has_character = player_id in snapshot.player_seats
                    gate_branch = "player_seats"
                elif not _is_mp:
                    has_character = bool(snapshot.characters)
                    gate_branch = "legacy_solo_any_character"
                elif display_name in _existing_char_names:
                    # MP back-fill: original player resuming a pre-binding
                    # save. Seat them now so subsequent joiners see the
                    # populated player_seats branch.
                    has_character = True
                    gate_branch = "mp_legacy_backfill"
                    snapshot.player_seats[player_id] = display_name
                    logger.info(
                        "session.player_seat_backfilled_on_resume "
                        "slug=%s player_id=%s character=%s",
                        slug,
                        player_id,
                        display_name,
                    )
                    _watcher_publish(
                        "session_player_seat_backfilled",
                        {
                            "slug": slug,
                            "player_id": player_id,
                            "character_name": display_name,
                            "reason": "mp_legacy_save_resume",
                        },
                        component="session",
                    )
                else:
                    # MP new joiner — route to chargen, do NOT auto-claim
                    # the existing PC. Emit a watcher event so the GM panel
                    # can see new joiners arriving.
                    has_character = False
                    gate_branch = "mp_new_joiner_chargen_required"
                    logger.info(
                        "session.mp_new_joiner_chargen_required "
                        "slug=%s player_id=%s display_name=%s "
                        "existing_characters=%s",
                        slug,
                        player_id,
                        display_name,
                        sorted(_existing_char_names),
                    )
                    _watcher_publish(
                        "mp_new_joiner_chargen_required",
                        {
                            "slug": slug,
                            "player_id": player_id,
                            "player_name": display_name,
                            "existing_character_names": sorted(_existing_char_names),
                            "character_count": len(snapshot.characters),
                        },
                        component="session",
                    )
                logger.info(
                    "session.chargen_gate slug=%s player_id=%s branch=%s "
                    "has_character=%s seat_count=%d character_count=%d",
                    slug,
                    player_id,
                    gate_branch,
                    has_character,
                    len(snapshot.player_seats),
                    len(snapshot.characters),
                )
                _watcher_publish(
                    "session_chargen_gate",
                    {
                        "slug": slug,
                        "player_id": player_id,
                        "branch": gate_branch,
                        "has_character": has_character,
                        "seat_count": len(snapshot.player_seats),
                        "character_count": len(snapshot.characters),
                        "seated_player_ids": list(snapshot.player_seats.keys()),
                    },
                    component="session",
                )
                # Rename-on-resume: pre-fix saves stored ``core.name`` as the
                # opaque player UUID because chargen used ``with_lobby_name``
                # AFTER the name fix landed. Detect the UUID pattern and
                # swap in the lobby display_name on resume, then persist so
                # the rename sticks and the next turn's PARTY_STATUS sees the
                # real name. See pingpong 2026-04-24 "Resumed character shows
                # UUID as name" (medium, user-visible everywhere).
                renamed = _rename_resumed_character_if_uuid(
                    snapshot=snapshot,
                    display_name=display_name,
                    player_id=player_id,
                )
                # ADR-037 Python port: bind the canonical snapshot to the
                # room BEFORE the rename-save below. Idempotent — if a peer
                # got here first, our load is discarded and we observe the
                # already-bound snapshot.
                room.bind_world(snapshot=snapshot, store=store)
                # All subsequent reads must come from the canonical room
                # binding (which may differ from our local ``snapshot`` if
                # we lost the bind race).
                snapshot = room.snapshot  # type: ignore[assignment]
                if renamed:
                    room.save()
                    logger.info(
                        "session.slug_resumed.renamed_uuid player_id=%s old=%s new=%s",
                        player_id,
                        player_id,  # equal to the pre-rename value
                        display_name,
                    )
                logger.info(
                    "session.slug_resumed genre=%s world=%s slug=%s turn=%s",
                    row.genre_slug,
                    row.world_slug,
                    slug,
                    snapshot.turn_manager.interaction,
                )
            else:
                snapshot = GameSnapshot(
                    genre_slug=row.genre_slug,
                    world_slug=row.world_slug,
                    location="Unknown",
                )
                store.init_session(row.genre_slug, row.world_slug)
                # ADR-037 Python port: bind the fresh snapshot to the room
                # so the second-connect handler observes the same object.
                room.bind_world(snapshot=snapshot, store=store)
                snapshot = room.snapshot  # type: ignore[assignment]
                has_character = False
                logger.info(
                    "session.slug_new_session genre=%s world=%s slug=%s",
                    row.genre_slug,
                    row.world_slug,
                    slug,
                )

            # Initialize chargen builder when entering Creating state. Slug
            # path parity with legacy branch (playtest 2026-04-23): without
            # this the client lands on an empty <CharacterCreation/> and has
            # no way to advance — there is no client-side kickoff. The lobby
            # name is the human-readable display name sent by the UI (see
            # display_name resolution above) — NOT the opaque player UUID.
            builder: CharacterBuilder | None = None
            if not has_character and genre_pack.char_creation:
                builder = CharacterBuilder(
                    scenes=list(genre_pack.char_creation),
                    rules=genre_pack.rules,
                    backstory_tables=genre_pack.backstory_tables,
                ).with_lobby_name(display_name)
                if genre_pack.equipment_tables is not None:
                    builder = builder.with_equipment_tables(genre_pack.equipment_tables)

            # Opening-hook + world-context resolution (matches legacy branch).
            # Resolved once at connect time so chargen confirmation and the
            # narrator's first turn see the same directive/seed/context.
            opening: tuple[str, str] | None = resolve_opening(
                genre_pack, row.world_slug, row.genre_slug
            )
            opening_seed: str | None = None
            opening_directive: str | None = None
            if opening is not None:
                opening_seed, opening_directive = opening

            # MP-joiner cold-open suppression (playtest 2026-04-26 "Multiplayer
            # parallel-solo desynchronizes scene context entirely"). When a
            # second player joins an in-progress MP session, they go through
            # chargen and then fire `_run_opening_turn_narration` at the end.
            # Without this guard the narrator gets a fresh kidnapping/
            # in-medias-res cold-open directive — which it dutifully obeys by
            # inventing a NEW scene ("THE THROAT", descending into the dungeon)
            # that has nothing to do with where the existing party already is
            # ("Sinkhole Inn Room"). The shared narrator session (ADR-067)
            # remembers the prior scene, but a fresh opening_directive in the
            # Early zone overrides scene continuity.
            #
            # Detection: this player has no character yet (going to chargen)
            # AND there is at least one character already on the snapshot
            # (someone else has already started the world). When both hold
            # we're an MP joiner — suppress opening so the post-chargen
            # narration falls back to the generic "I look around and take in
            # my surroundings." which the persistent narrator handles as a
            # continuation of the existing scene.
            is_mp_joiner = not has_character and len(snapshot.characters) > 0
            if is_mp_joiner:
                _watcher_publish(
                    "mp_joiner_opening_suppressed",
                    {
                        "slug": slug,
                        "player_id": player_id,
                        "player_name": display_name,
                        "existing_character_count": len(snapshot.characters),
                        "had_seed": opening_seed is not None,
                        "had_directive": opening_directive is not None,
                    },
                    component="opening_hook",
                    severity="info",
                )
                logger.info(
                    "session.mp_joiner_opening_suppressed slug=%s "
                    "player_id=%s display_name=%s existing_chars=%d",
                    slug,
                    player_id,
                    display_name,
                    len(snapshot.characters),
                )
                opening_seed = None
                opening_directive = None
            culture_ref = resolve_culture_reference(genre_pack, row.world_slug)
            world_context: str | None = culture_ref if culture_ref else None
            audio_backend = self._build_audio_backend(row.genre_slug, genre_pack)

            # ADR-067 single-narrator-per-slug: get the canonical
            # orchestrator from the room (constructing it lazily on
            # first connect). A per-session Orchestrator would create a
            # second Claude --resume id and produce divergent narration
            # for each player on the slug — playtest 2026-04-26 "MP —
            # parallel solo games" root cause.
            shared_orchestrator = room.get_or_create_orchestrator(
                lambda: Orchestrator(client=self._client_factory())
            )
            self._session_data = _SessionData(
                genre_slug=row.genre_slug,
                world_slug=row.world_slug,
                player_name=display_name,
                player_id=player_id,
                # ADR-037 Python port: take snapshot/store directly from the
                # canonical room binding so future readers see the contract
                # explicitly. Equivalent to the local ``snapshot``/``store``
                # references after the idempotent ``bind_world`` above.
                snapshot=room.snapshot,
                store=room.store,
                genre_pack=genre_pack,
                orchestrator=shared_orchestrator,
                local_dm=LocalDM(client=self._client_factory()),
                builder=builder,
                opening_seed=opening_seed,
                opening_directive=opening_directive,
                world_context=world_context,
                audio_backend=audio_backend,
                game_slug=slug,
                mode=GameMode(row.mode),
                # ADR-050: pick the cooldown that matches the session mode.
                # MP defaults to 60s because turns resolve faster in group
                # play; solo gets the more responsive 30s window.
                image_pacing_throttle=(
                    ImagePacingThrottle.for_multiplayer()
                    if GameMode(row.mode) == GameMode.MULTIPLAYER
                    else ImagePacingThrottle.for_solo()
                ),
            )
            # MP-03 Task 3 + Task-17 + Task-22 ProjectionFilter Rules integration.
            self._event_log = EventLog(store)
            self._projection_cache = ProjectionCache(store)
            projection_rules = genre_pack.projection_rules
            if projection_rules is not None:
                self._projection_filter = ComposedFilter(
                    rules=projection_rules,
                    pack_slug=row.genre_slug,
                )
            else:
                self._projection_filter = ComposedFilter.with_no_genre_rules()
            self._last_seen_seq = payload.last_seen_seq or 0
            self._current_player_id = player_id
            self._state = _State.Creating if not has_character else _State.Playing
            connected_msg = SessionEventMessage(
                type="SESSION_EVENT",  # type: ignore[arg-type]
                payload=SessionEventPayload(
                    event="connected",
                    player_name=display_name,
                    genre=row.genre_slug,
                    world=row.world_slug,
                    has_character=has_character,
                ),
                player_id=player_id,
            )

            # Task 19: lazy-fill projection_cache for this player if they're
            # joining a session that has events already. Subsequent reconnects
            # read from cache (Task 18) — no re-filter.
            if self._projection_cache is not None:
                from sidequest.game.projection.cache_fill import lazy_fill

                lazy_fill(
                    event_log=self._event_log,
                    cache=self._projection_cache,
                    filter_=self._projection_filter,
                    view=self._build_game_state_view(),
                    player_id=player_id,
                )

            # MP-03 Task 4 / ProjectionFilter-Rules Task 18: replay from
            # projection_cache when present (byte-identical to what the live
            # player received). Legacy fallback runs filter live.
            #
            # Lie-detector for pingpong 2026-04-24 "Empty narrative on
            # resume" — the cache read + per-kind replay path are the
            # failure-prone hot spots. Record the cache row counts, the
            # include/exclude split, and the by-kind distribution on the
            # current span so the GM dashboard can tell at a glance
            # whether the replay had zero NARRATIONs (bug) or the UI
            # dropped them after delivery (different bug).
            replay_msgs: list[object] = []
            _replay_span = trace.get_current_span()
            _replay_cache_rows = 0
            _replay_excluded = 0
            _replay_kind_lookup_miss = 0
            _replay_skipped_internal = 0
            _replay_kinds: dict[str, int] = {}
            if self._projection_cache is not None:
                cached_rows = self._projection_cache.read_since(
                    player_id=self._current_player_id,
                    since_seq=self._last_seen_seq,
                )
                _replay_cache_rows = len(cached_rows)
                for c in cached_rows:
                    if not c.include or c.payload_json is None:
                        _replay_excluded += 1
                        continue
                    # Need the event kind to rebuild the message — look it up.
                    # Most sessions won't have many missed events on reconnect,
                    # but this does one event-log read per cache row. Acceptable
                    # for v1; optimize to a join query if it becomes hot.
                    kind_lookup = self._event_log.read_since(since_seq=c.event_seq - 1)
                    if not kind_lookup or kind_lookup[0].seq != c.event_seq:
                        _replay_kind_lookup_miss += 1
                        continue
                    _kind = kind_lookup[0].kind
                    _built = _build_message_for_kind(
                        kind=_kind,
                        payload_json=c.payload_json,
                        seq=c.event_seq,
                    )
                    if _built is None:
                        # Internal telemetry kind (encounter*, etc.) — not
                        # client-bound. Skip without crashing replay.
                        _replay_skipped_internal += 1
                        continue
                    _replay_kinds[_kind] = _replay_kinds.get(_kind, 0) + 1
                    replay_msgs.append(_built)
            else:
                # Legacy fallback: no cache available, filter live (may diverge
                # from cached projections in edge cases; v1 accepts this).
                missed = self._event_log.read_since(since_seq=self._last_seen_seq)
                view = self._build_game_state_view()
                for event_row in missed:
                    envelope = MessageEnvelope(
                        kind=event_row.kind,
                        payload_json=event_row.payload_json,
                        origin_seq=event_row.seq,
                    )
                    dec = self._projection_filter.project(
                        envelope=envelope, view=view, player_id=self._current_player_id
                    )
                    if not dec.include:
                        continue
                    _built = _build_message_for_kind(
                        kind=event_row.kind,
                        payload_json=dec.payload_json,
                        seq=event_row.seq,
                    )
                    if _built is None:
                        _replay_skipped_internal += 1
                        continue
                    _replay_kinds[event_row.kind] = _replay_kinds.get(event_row.kind, 0) + 1
                    replay_msgs.append(_built)

            # Pingpong 2026-04-24 "Slug-resume shows empty Narrative pane on
            # fresh browser session" — when the client's stored
            # ``last_seen_seq`` already points at (or past) the most recent
            # NARRATION, the replay loop above correctly emits zero narration
            # messages. For a live resume that's the right behavior; for a
            # fresh-browser resume the narrative pane paints blank because
            # nothing the user can read is on-screen. Backfill the most
            # recent NARRATION (plus any CHAPTER_MARKER that preceded it
            # without an intervening narration) from the cache, regardless of
            # ``last_seen_seq``, so the pane has at least the last chapter to
            # render. Bounded to 2 messages to avoid dumping a long replay.
            tail_backfill_count = 0
            if _replay_kinds.get("NARRATION", 0) == 0 and self._projection_cache is not None:
                tail_msgs = self._backfill_last_narration_block(
                    player_id=self._current_player_id,
                )
                if tail_msgs:
                    replay_msgs.extend(tail_msgs)
                    tail_backfill_count = len(tail_msgs)
                    for msg in tail_msgs:
                        kind_name = getattr(msg, "type", "")
                        _replay_kinds[kind_name] = _replay_kinds.get(kind_name, 0) + 1

            # Always record the replay outcome — zero-replay is a bug signal
            # the GM panel needs to see (pingpong 2026-04-24 "empty narrative
            # on resume"). Attributes stay on the current mp.slug_connect
            # span; matching log line for grep-friendly tailing.
            _replay_span.set_attribute("slug_connect.replay.cache_rows", _replay_cache_rows)
            _replay_span.set_attribute("slug_connect.replay.excluded", _replay_excluded)
            _replay_span.set_attribute(
                "slug_connect.replay.kind_lookup_miss", _replay_kind_lookup_miss
            )
            _replay_span.set_attribute(
                "slug_connect.replay.skipped_internal", _replay_skipped_internal
            )
            _replay_span.set_attribute("slug_connect.replay.emitted", len(replay_msgs))
            _replay_span.set_attribute(
                "slug_connect.replay.narration_count",
                _replay_kinds.get("NARRATION", 0),
            )
            _replay_span.set_attribute(
                "slug_connect.replay.tail_backfill_count", tail_backfill_count
            )
            _replay_span.set_attribute("slug_connect.replay.last_seen_seq", self._last_seen_seq)
            logger.info(
                "slug_connect.replay cache_rows=%d excluded=%d skipped_internal=%d "
                "emitted=%d narration=%d tail_backfill=%d last_seen_seq=%d "
                "player=%s slug=%s kinds=%s",
                _replay_cache_rows,
                _replay_excluded,
                _replay_skipped_internal,
                len(replay_msgs),
                _replay_kinds.get("NARRATION", 0),
                tail_backfill_count,
                self._last_seen_seq,
                self._current_player_id,
                slug,
                dict(_replay_kinds),
            )

            # Bootstrap messages (playtest 2026-04-23 parity with legacy
            # connect path). Without these the client lands on an empty
            # <CharacterCreation/> (Creating) or stays on ConnectScreen
            # forever (Playing).
            bootstrap_msgs: list[object] = []
            if self._state is _State.Creating and builder is not None:
                bootstrap_msgs.append(builder.to_scene_message(player_id))
                # OTEL: let the GM panel verify chargen bootstrap fired —
                # lie detector for "did this subsystem actually engage?".
                # Emitted as a child of mp.slug_connect via a fresh span
                # (the mp span has already closed by this point).
                _bootstrap_tracer = trace.get_tracer("sidequest.server.session_handler")
                with _bootstrap_tracer.start_as_current_span(
                    "slug_connect.chargen_bootstrap"
                ) as _bootstrap_span:
                    _bootstrap_span.set_attribute("player_id", player_id)
                    _bootstrap_span.set_attribute("slug", slug)
                    _bootstrap_span.set_attribute("scene_index", builder.current_scene_index())
            elif self._state is _State.Playing:
                ready_msg = SessionEventMessage(
                    type="SESSION_EVENT",  # type: ignore[arg-type]
                    payload=SessionEventPayload(
                        event="ready",
                        player_name=display_name,
                        genre=row.genre_slug,
                        world=row.world_slug,
                        has_character=True,
                        initial_state=None,
                        css=None,
                        narrator_verbosity=None,
                        narrator_vocabulary=None,
                        image_cooldown_seconds=None,
                    ),
                    player_id=player_id,
                )
                bootstrap_msgs.append(ready_msg)
                logger.info(
                    "session.ready_emitted reason=slug_resume player=%s turn=%d",
                    player_id,
                    snapshot.turn_manager.interaction,
                )
                # Snapshot the resumed session so the dashboard State tab and
                # Subsystems "session" component light up on reconnect.
                _watcher_publish(
                    "game_state_snapshot",
                    {
                        "reason": "resume",
                        "genre_slug": row.genre_slug,
                        "world_slug": row.world_slug,
                        "player_name": display_name,
                        "player_id": player_id,
                        "turn_number": snapshot.turn_manager.interaction,
                        "current_location": snapshot.location or "",
                        "discovered_regions": list(snapshot.discovered_regions),
                        "npc_registry_count": len(snapshot.npc_registry),
                        "quest_log_count": len(snapshot.quest_log),
                        "lore_established_count": len(snapshot.lore_established),
                        "character_count": len(snapshot.characters),
                    },
                    component="session",
                )
                # Refresh PARTY_STATUS so the resumed client's header / sheet
                # / location update from the saved snapshot. MP: resolve
                # self by player_id (snapshot.player_seats / room seat),
                # not snapshot.characters[0] — picking the first PC mis-
                # tags every non-first player's data with their own
                # player_id (playtest 2026-04-25 "Tab 2 sees Laverne (YOU)").
                if snapshot.characters:
                    try:
                        self_char = (
                            self._resolve_self_character(self._session_data)
                            or snapshot.characters[0]
                        )
                        bootstrap_msgs.append(
                            self._build_session_start_party_status(
                                self._session_data, self_char, player_id
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("session.resume_party_status_failed error=%s", exc)
                # CHAPTER_MARKER — restore the running-header chapter title
                # on resume so the saved location shows up immediately
                # (not only after the next narration turn). Pingpong
                # 2026-04-24 "Location not rendered in the header on
                # resume": the client's ``useRunningHeader`` hook reads
                # CHAPTER_MARKER events but the server was never emitting
                # them — orphan protocol type. Emit once here; subsequent
                # turns emit their own CHAPTER_MARKER when the narrator
                # changes location (see _execute_narration_turn).
                if snapshot.location:
                    bootstrap_msgs.append(
                        ChapterMarkerMessage(
                            payload=ChapterMarkerPayload(
                                title=None,
                                location=_resolve_location_display(
                                    self._session_data.genre_pack
                                    if self._session_data is not None
                                    else None,
                                    row.world_slug,
                                    snapshot.location,
                                ),
                            ),
                            player_id=player_id,
                        )
                    )

            return [connected_msg, *bootstrap_msgs, *replay_msgs]

        genre_slug = payload.genre or ""
        world_slug = payload.world or ""
        player_name = payload.player_name or "player"

        if not genre_slug:
            return [_error_msg("SESSION_EVENT{connect} missing genre slug")]
        if not world_slug:
            return [_error_msg("SESSION_EVENT{connect} missing world slug")]

        logger.info(
            "session.connect genre=%s world=%s player=%s",
            genre_slug,
            world_slug,
            player_name,
        )

        # Generate a stable player_id if not provided by client
        if not player_id:
            player_id = str(uuid.uuid4())

        # Load genre pack
        try:
            loader = GenreLoader(search_paths=self._search_paths)
            genre_pack = loader.load(genre_slug)
        except Exception as exc:
            logger.error("session.genre_load_failed genre=%s error=%s", genre_slug, exc)
            return [_error_msg(f"Failed to load genre pack '{genre_slug}': {exc}")]

        # Open or create SQLite save
        db_path = db_path_for_session(self._save_dir, genre_slug, world_slug, player_name)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = SqliteStore.open(str(db_path))
        from sidequest.telemetry.watcher_hub import bind_event_store as _bind_event_store

        _bind_event_store(store)

        # Load existing session or start fresh. Schema-incompatible saves
        # (legacy snapshots that fail current GameSnapshot validation)
        # surface as a typed ERROR with code=save_schema_invalid; see the
        # `_handle_connect` slug branch for the rationale.
        try:
            saved = store.load()
        except SaveSchemaIncompatibleError as exc:
            logger.warning(
                "session.save_schema_invalid genre=%s world=%s player=%s path=%s error=%s",
                genre_slug,
                world_slug,
                player_name,
                exc.save_path,
                exc.underlying,
            )
            _watcher_publish(
                "save_schema_invalid",
                {
                    "genre_slug": genre_slug,
                    "world_slug": world_slug,
                    "player_name": player_name,
                    "save_path": str(exc.save_path),
                    "validation_error": str(exc.underlying),
                },
                component="session",
                severity="error",
            )
            return [
                _error_msg(
                    f"This save predates the current schema and cannot be "
                    f"loaded. Start a new adventure or move the save aside: "
                    f"{exc.save_path}",
                    reconnect_required=False,
                    code="save_schema_invalid",
                )
            ]
        has_character: bool
        if saved is not None:
            snapshot = saved.snapshot
            has_character = bool(snapshot.characters)
            logger.info(
                "session.resumed genre=%s world=%s player=%s turn=%s",
                genre_slug,
                world_slug,
                player_name,
                snapshot.turn_manager.interaction,
            )
        else:
            snapshot = GameSnapshot(
                genre_slug=genre_slug,
                world_slug=world_slug,
                location="Unknown",
            )
            store.init_session(genre_slug, world_slug)
            has_character = False
            logger.info(
                "session.new_session genre=%s world=%s player=%s",
                genre_slug,
                world_slug,
                player_name,
            )

        # Build orchestrator (one per session, persistent session ADR-066)
        orchestrator = Orchestrator(client=self._client_factory())

        # Initialize chargen builder when entering Creating state. The lobby
        # name is the fallback the Name line uses when the genre has no
        # name-entry scene (caverns_and_claudes, etc.).
        builder: CharacterBuilder | None = None
        if not has_character and genre_pack.char_creation:
            builder = CharacterBuilder(
                scenes=list(genre_pack.char_creation),
                rules=genre_pack.rules,
                backstory_tables=genre_pack.backstory_tables,
            ).with_lobby_name(player_name)
            if genre_pack.equipment_tables is not None:
                builder = builder.with_equipment_tables(genre_pack.equipment_tables)

        # Opening-hook resolution (Story 2.3 Slice B). Pick one hook per
        # connection from world.openings (preferred) or pack.openings, so
        # the first narrator turn has an ``opening_directive`` to inject
        # and an ``opening_seed`` to run as the first action. ``None`` on
        # both when the pack has no openings configured — first turn
        # runs without a directive. Returning-player reconnects (has_
        # character=True) skip chargen, so the directive/seed are dead
        # weight for them, but resolving here anyway keeps the seat
        # uniform and costs nothing.
        opening: tuple[str, str] | None = resolve_opening(genre_pack, world_slug, genre_slug)
        opening_seed: str | None = None
        opening_directive: str | None = None
        if opening is not None:
            opening_seed, opening_directive = opening

        # World context (Story 41-11): resolve once at connect time so
        # the filter engages consistently across every turn. Empty
        # reference → ``None`` so the orchestrator skips the section
        # instead of registering an empty block.
        culture_ref = resolve_culture_reference(genre_pack, world_slug)
        world_context: str | None = culture_ref if culture_ref else None
        audio_backend = self._build_audio_backend(genre_slug, genre_pack)

        self._session_data = _SessionData(
            genre_slug=genre_slug,
            world_slug=world_slug,
            player_name=player_name,
            player_id=player_id,
            snapshot=snapshot,
            store=store,
            genre_pack=genre_pack,
            orchestrator=orchestrator,
            local_dm=LocalDM(client=self._client_factory()),
            builder=builder,
            opening_seed=opening_seed,
            opening_directive=opening_directive,
            world_context=world_context,
            audio_backend=audio_backend,
        )
        self._state = _State.Creating if not has_character else _State.Playing

        connected_msg = SessionEventMessage(
            type="SESSION_EVENT",  # type: ignore[arg-type]
            payload=SessionEventPayload(
                event="connected",
                player_name=player_name,
                genre=genre_slug,
                world=world_slug,
                has_character=has_character,
                initial_state=None,
                css=None,
                narrator_verbosity=None,
                narrator_vocabulary=None,
                image_cooldown_seconds=None,
            ),
            player_id=player_id,
        )

        # Kick off chargen by emitting the first scene alongside the
        # connected event when we're entering Creating state. Without
        # this the client lands on an empty <CharacterCreation/> and
        # has no way to advance — there is no client-side kickoff.
        outbound: list[object] = [connected_msg]
        if self._state is _State.Creating and builder is not None:
            outbound.append(builder.to_scene_message(player_id))

        # Resume path: when has_character=True the client needs a
        # `SESSION_EVENT{event:"ready"}` to flip sessionPhase from
        # "connect" to "game". Without this the returning player stays
        # on the ConnectScreen forever even though the server has
        # resumed their save (playtest 2026-04-22). App.tsx handles
        # chargen-complete → "game" directly, so the ready event is
        # only needed on the resume branch.
        if self._state is _State.Playing:
            ready_msg = SessionEventMessage(
                type="SESSION_EVENT",  # type: ignore[arg-type]
                payload=SessionEventPayload(
                    event="ready",
                    player_name=player_name,
                    genre=genre_slug,
                    world=world_slug,
                    has_character=True,
                    initial_state=None,
                    css=None,
                    narrator_verbosity=None,
                    narrator_vocabulary=None,
                    image_cooldown_seconds=None,
                ),
                player_id=player_id,
            )
            outbound.append(ready_msg)
            logger.info(
                "session.ready_emitted reason=resume player=%s turn=%d",
                player_name,
                snapshot.turn_manager.interaction,
            )
            # Snapshot the resumed session so the dashboard State tab and
            # Subsystems "session" component light up on reconnect without
            # waiting for the next narration turn.
            _watcher_publish(
                "game_state_snapshot",
                {
                    "reason": "resume",
                    "genre_slug": genre_slug,
                    "world_slug": world_slug,
                    "player_name": player_name,
                    "player_id": player_id,
                    "turn_number": snapshot.turn_manager.interaction,
                    "current_location": snapshot.location or "",
                    "discovered_regions": list(snapshot.discovered_regions),
                    "npc_registry_count": len(snapshot.npc_registry),
                    "quest_log_count": len(snapshot.quest_log),
                    "lore_established_count": len(snapshot.lore_established),
                    "character_count": len(snapshot.characters),
                },
                component="session",
            )
            # Also refresh PARTY_STATUS so the resumed client's header /
            # sheet / location update from the saved snapshot. Without
            # this the UI has no character data until the next narration
            # turn fires. MP: resolve self by player_id (see notes at the
            # other call site, ~line 1640).
            if snapshot.characters:
                try:
                    self_char = (
                        self._resolve_self_character(self._session_data) or snapshot.characters[0]
                    )
                    outbound.append(
                        self._build_session_start_party_status(
                            self._session_data, self_char, player_id
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("session.resume_party_status_failed error=%s", exc)

        return outbound

    # ------------------------------------------------------------------
    # CHARACTER_CREATION dispatch
    # ------------------------------------------------------------------

    async def _handle_character_creation(self, msg: GameMessage) -> list[object]:
        """Route CHARACTER_CREATION traffic through the chargen state machine.

        Port of ``dispatch_character_creation`` in
        ``sidequest-api/crates/sidequest-server/src/dispatch/connect.rs``.

        Navigation actions (back / edit) are handled before phase dispatch;
        the UI sends them as a separate channel that can fire in any phase.
        Phase dispatch covers ``scene`` (player submitted a choice or
        freeform answer), ``continue`` (player acknowledged a display-only
        scene), and ``confirmation`` (player committed — builder.build()
        runs and the Character lands on snapshot).

        Every error path returns a structured ERROR message rather than
        raising — the WebSocket contract says we never leak exceptions to
        the client (2.2 acceptance: "Invalid inputs produce structured error
        messages, never exceptions through the WebSocket").

        Starting-equipment wiring from ``pack.inventory.starting_equipment``
        and archetype-resolver wiring (``resolve_archetype`` into
        ``character.resolved_archetype``) are deferred to Story 2.3 — their
        seat is right after ``builder.build()`` below, next to the SQLite
        save and world-materialization pipeline.
        """
        if self._state != _State.Creating:
            return [
                _error_msg(
                    f"Cannot process CHARACTER_CREATION: session state is "
                    f"{self._state.name}, expected Creating"
                )
            ]
        if self._session_data is None:
            return [_error_msg("Internal error: session data missing")]
        sd = self._session_data
        if sd.builder is None:
            return [
                _error_msg(
                    f"No character builder active for genre '{sd.genre_slug}' "
                    f"— genre pack has no character_creation scenes"
                )
            ]

        builder = sd.builder
        payload: CharacterCreationPayload = msg.payload  # type: ignore[attr-defined]
        player_id: str = getattr(msg, "player_id", "") or sd.player_id
        span = trace.get_current_span()

        # ---- Navigation actions (back / edit / unknown) -------------------
        if payload.action is not None:
            action = payload.action
            if action == "back":
                span.add_event(
                    "character_creation.back",
                    {
                        "action": "back",
                        "from_scene": builder.current_scene_index(),
                        "player_id": player_id,
                    },
                )
                try:
                    builder.go_back()
                except BuilderError as exc:
                    return [_error_msg(f"Cannot go back: {exc!r}")]
                return [builder.to_scene_message(player_id)]

            if action == "edit":
                if payload.target_step is None:
                    return [_error_msg("action:edit requires target_step field")]
                target = payload.target_step
                span.add_event(
                    "character_creation.edit",
                    {
                        "action": "edit",
                        "target_step": target,
                        "player_id": player_id,
                    },
                )
                try:
                    builder.go_to_scene(target)
                except BuilderError as exc:
                    return [_error_msg(f"Cannot edit scene {target}: {exc!r}")]
                return [builder.to_scene_message(player_id)]

            return [_error_msg(f"Unknown chargen action: {action}")]

        # ---- Phase dispatch ----------------------------------------------
        phase = payload.phase
        logger.info("chargen.phase phase=%s player_id=%s", phase, player_id)

        if phase == "scene":
            return self._chargen_scene(builder, payload, sd, player_id, span)
        if phase == "continue":
            return self._chargen_continue(builder, sd, player_id, span)
        if phase == "confirmation":
            return await self._chargen_confirmation(builder, sd, player_id, span)
        return [_error_msg(f"Unknown chargen phase: {phase}")]

    # ---- phase=scene ----------------------------------------------------
    def _chargen_scene(
        self,
        builder: CharacterBuilder,
        payload: CharacterCreationPayload,
        sd: _SessionData,
        player_id: str,
        span: trace.Span,
    ) -> list[object]:
        choice_str = payload.choice if payload.choice is not None else "1"

        resolved_index: int | None
        try:
            # 1-based numeric index (Rust: saturating_sub(1))
            n = int(choice_str)
            resolved_index = max(0, n - 1)
        except ValueError:
            # Label-match (case-insensitive) against the current scene's
            # choices. Only applies when we're in InProgress; AwaitingFollowup
            # has no choice list and will fall through to freeform below.
            resolved_index = None
            if builder.is_in_progress():
                current = builder.current_scene()
                for i, c in enumerate(current.choices):
                    if c.label.casefold() == choice_str.casefold():
                        resolved_index = i
                        break

        span.add_event(
            "character_creation.scene",
            {
                "phase": "scene",
                "choice_raw": choice_str,
                "resolved_index": str(resolved_index),
                "player_id": player_id,
            },
        )

        if resolved_index is not None:
            try:
                builder.apply_choice(resolved_index)
            except BuilderError as exc:
                return [_error_msg(f"Invalid choice: {exc!r}")]
        else:
            try:
                builder.apply_freeform(choice_str)
            except BuilderError as exc:
                return [_error_msg(f"Invalid freeform input: {exc!r}")]

        return self._next_message(builder, sd, player_id)

    # ---- phase=continue -------------------------------------------------
    def _chargen_continue(
        self,
        builder: CharacterBuilder,
        sd: _SessionData,
        player_id: str,
        span: trace.Span,
    ) -> list[object]:
        logger.info("chargen.continue player_id=%s", player_id)
        span.add_event(
            "character_creation.continue",
            {"phase": "continue", "player_id": player_id},
        )
        try:
            builder.apply_auto_advance()
        except BuilderError as exc:
            return [_error_msg(f"Cannot continue from current scene: {exc!r}")]
        return self._next_message(builder, sd, player_id)

    # ---- archetype resolution helper (Story 2.3 Slice A) ----------------
    def _resolve_character_archetype(
        self,
        character: Character,
        sd: _SessionData,
        player_id: str,
        span: trace.Span,
    ) -> None:
        """Resolve a raw ``jungian/rpg_role`` pair through the archetype shim.

        The builder encodes accumulated archetype hints as
        ``f"{jungian}/{rpg_role}"`` on ``character.resolved_archetype`` (see
        ``builder.py:1640-1645``). This helper detects that raw form, runs
        the four-tier resolve (base → constraints → world funnels), and
        replaces the raw pair with the resolved display name via
        ``apply_archetype_resolved`` — keeping ``archetype_provenance`` in
        lockstep.

        Resolution failures emit a ``character_creation.archetype_resolution_failed``
        span event and leave the raw pair in place (non-fatal for chargen —
        the GM panel can still see the attempt). Missing axis data on the
        pack (no ``base_archetypes`` or ``archetype_constraints``) silently
        no-ops: the pack chose not to use archetype axes.

        Rust parity: ``connect.rs:1644-1737``.
        """
        raw = character.resolved_archetype
        if raw is None or "/" not in raw:
            return

        jungian, rpg_role = raw.split("/", 1)
        pack = sd.genre_pack
        if pack.base_archetypes is None or pack.archetype_constraints is None:
            return

        world = pack.worlds.get(sd.world_slug)
        funnels = world.archetype_funnels if world is not None else None

        try:
            resolution = resolve_archetype(
                jungian=jungian,
                rpg_role=rpg_role,
                base=pack.base_archetypes,
                constraints=pack.archetype_constraints,
                funnels=funnels,
                genre=sd.genre_slug,
                world=sd.world_slug,
            )
        except GenreValidationError as exc:
            span.add_event(
                "character_creation.archetype_resolution_failed",
                {
                    "event": "archetype.resolution_failed",
                    "error": str(exc),
                    "jungian": jungian,
                    "rpg_role": rpg_role,
                    "player_id": player_id,
                },
            )
            logger.warning(
                "chargen.archetype_resolution_failed jungian=%s rpg_role=%s error=%s",
                jungian,
                rpg_role,
                exc,
            )
            return

        apply_archetype_resolved(character, resolution)
        span.add_event(
            "character_creation.archetype_resolved",
            {
                "event": "archetype.resolved",
                "jungian": jungian,
                "rpg_role": rpg_role,
                "resolved_name": resolution.resolved.name,
                "source": resolution.source.value,
                "source_tier": resolution.provenance.source_tier.value,
                "weight": resolution.weight.value,
                "faction": resolution.resolved.faction or "none",
                "genre": sd.genre_slug,
                "world": sd.world_slug,
                "player_id": player_id,
            },
        )

    # ---- phase=confirmation (commit) ------------------------------------
    async def _chargen_confirmation(
        self,
        builder: CharacterBuilder,
        sd: _SessionData,
        player_id: str,
        span: trace.Span,
    ) -> list[object]:
        # Name resolution: scene > lobby > "Player". Do NOT fall back to
        # payload.choice — that's the UI button index (e.g. "1"), not a
        # name (Rust comment at connect.rs:1607).
        name_from_scene = builder.character_name()
        char_name = name_from_scene or sd.player_name or "Player"
        source = "name_scene" if name_from_scene is not None else "player_name_fallback"
        span.add_event(
            "character_creation.name_resolved",
            {
                "event": "name_resolved",
                "char_name": char_name,
                "source": source,
                "player_id": player_id,
            },
        )

        try:
            character = builder.build(char_name)
        except BuilderError as exc:
            return [_error_msg(f"Character build failed: {exc!r}")]

        # ADR-014 / ADR-078: emit edge.current/.max instead of `hp` — the
        # field formerly labelled `hp` was already pulling from edge, so the
        # name was misleading the OTEL dashboard. `schema=adr-014` lets us
        # find the rename in audits.
        span.add_event(
            "character_creation.character_built",
            {
                "event": "character_built",
                "schema": "adr-014",
                "name": character.core.name,
                "class": character.char_class,
                "race": character.race,
                "edge_current": character.core.edge.current,
                "edge_max": character.core.edge.max,
                "player_id": player_id,
            },
        )

        # Archetype resolution (Story 2.3 Slice A). The builder writes
        # a raw "jungian/rpg_role" pair into ``resolved_archetype`` when
        # both axis hints were accumulated during chargen. Resolve it
        # through the four-tier shim (base → constraints → world funnels)
        # and replace the raw pair with the resolved display name, also
        # stamping ``archetype_provenance`` so the GM panel can show the
        # source tier. Rust parity: connect.rs:1644-1737.
        self._resolve_character_archetype(character, sd, player_id, span)

        # Starting equipment loadout (Story 2.3 Slice A). The builder only
        # holds item_hints; the class-specific loadout from inventory.yaml
        # is wired in here. Rust parity: connect.rs:1745-1864.
        apply_starting_loadout(character, sd.genre_pack.inventory)

        # MP: peer may have already committed on the same slug. The
        # ADR-037 Python port has every WS session on a slug share the
        # room's canonical ``GameSnapshot`` reference, so an in-memory
        # check is the authoritative one. Disk reload here used to
        # paper over the snapshot-orphaning bug (sd.snapshot reassignment
        # below) by re-fetching the peer's persisted state, but the
        # peer's ``room.save()`` could only write the stale (empty)
        # bound snapshot — so the disk-read returned no characters and
        # the second player wrongly took the first-commit branch. With
        # the canonical snapshot mutated in place (``replace_with``
        # below), in-memory ``sd.snapshot.characters`` is always live.
        existing_chars: list = list(sd.snapshot.characters)
        is_first_commit = not existing_chars

        if is_first_commit:
            # World materialization (Story 2.3 Slice C / Rust connect.rs:1892).
            # Parse failure → empty-snapshot fallback (must not hard-fail mid-
            # commit when the character is already built).
            try:
                materialized = materialize_from_genre_pack(
                    _world_history_value(sd.genre_pack, sd.world_slug),
                    CampaignMaturity.Fresh,
                    sd.genre_slug,
                    sd.world_slug,
                )
            except HistoryParseError as exc:
                logger.warning(
                    "world_materialization.parse_failed genre=%s world=%s error=%s",
                    sd.genre_slug,
                    sd.world_slug,
                    exc,
                )
                materialized = GameSnapshot(genre_slug=sd.genre_slug, world_slug=sd.world_slug)
            # Discard the "Adventurer" placeholder the fresh chapter may
            # author — the chargen-built character owns that slot.
            materialized.characters = [character]
            # Mutate the canonical room snapshot in place rather than
            # reassigning ``sd.snapshot``. Reassignment orphans the
            # ``room._snapshot`` reference: ``room.save()`` then
            # persists the stale pre-chargen snapshot, the next
            # connecting peer loads an empty save, treats themselves
            # as first-commit, materializes their own world, and the
            # slug ends up running two parallel solo games. Mutating
            # in place keeps every existing ``sd.snapshot`` /
            # ``room.snapshot`` reference live and pointing at the
            # same authoritative object — including any peer session
            # already bound to this slug.
            sd.snapshot.replace_with(materialized)
            span.add_event(
                "character_creation.world_materialized",
                {
                    "event": "world_materialized",
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "chapters_applied": len(materialized.world_history),
                    "maturity": materialized.campaign_maturity,
                    "trigger": "new_player_chargen",
                    "player_id": player_id,
                },
            )

            # Scenario binding (Slice D / connect.rs:1948). No-op without
            # scenarios; sets active_scenario on the session for later
            # pressure / scene-budget / accusation consumers.
            bind_result = bind_scenario(
                sd.genre_pack,
                sd.snapshot,
                genre_slug=sd.genre_slug,
                world_slug=sd.world_slug,
            )
            if bind_result is not None:
                _, active_pack = bind_result
                sd.active_scenario = active_pack

            world = sd.genre_pack.worlds.get(sd.world_slug)

            # Region init (Story 37-31). Runs for every world with
            # cartography. Init errors are pack-authoring bugs — log loud,
            # don't hard-fail the confirmation frame.
            if world is not None:
                try:
                    region_id = init_region_location(sd.snapshot, world.cartography)
                    span.add_event(
                        "region.initialized",
                        {
                            "event": "region.initialized",
                            "region": region_id,
                            "mode": world.cartography.navigation_mode.value,
                            "source": "starting_region",
                            "genre": sd.genre_slug,
                            "world": sd.world_slug,
                        },
                    )
                    logger.info(
                        "region.init genre=%s world=%s region=%s discovered_regions=%d",
                        sd.genre_slug,
                        sd.world_slug,
                        region_id,
                        len(sd.snapshot.discovered_regions),
                    )
                except RegionInitError as exc:
                    logger.error(
                        "region.init_failed genre=%s world=%s error=%s",
                        sd.genre_slug,
                        sd.world_slug,
                        exc,
                    )
                    span.add_event(
                        "region.init_failed",
                        {
                            "event": "region.init_failed",
                            "mode": world.cartography.navigation_mode.value,
                            "genre": sd.genre_slug,
                            "world": sd.world_slug,
                            "error": str(exc),
                        },
                    )

            if (
                world is not None
                and world.cartography.navigation_mode == NavigationMode.room_graph
                and world.cartography.rooms
            ):
                try:
                    entrance_id = init_room_graph_location(
                        sd.snapshot, list(world.cartography.rooms)
                    )
                    span.add_event(
                        "location.initialized",
                        {
                            "event": "location.initialized",
                            "location": entrance_id,
                            "mode": "room_graph",
                            "source": "entrance_room",
                            "genre": sd.genre_slug,
                            "world": sd.world_slug,
                        },
                    )
                    logger.info(
                        "room_graph.init genre=%s world=%s entrance=%s discovered_rooms=%d",
                        sd.genre_slug,
                        sd.world_slug,
                        entrance_id,
                        len(sd.snapshot.discovered_rooms),
                    )
                except RoomGraphInitError as exc:
                    logger.error(
                        "room_graph.init_failed genre=%s world=%s error=%s",
                        sd.genre_slug,
                        sd.world_slug,
                        exc,
                    )
                    span.add_event(
                        "location.init_failed",
                        {
                            "event": "location.init_failed",
                            "mode": "room_graph",
                            "genre": sd.genre_slug,
                            "world": sd.world_slug,
                            "error": str(exc),
                        },
                    )
        else:
            # MP second commit. ADR-037 Python port: sd.snapshot is the
            # canonical room snapshot (already populated by the first
            # committer's bind_world); just append our PC if not already
            # present. No reload from store — the in-memory snapshot is
            # authoritative.
            existing_names = {c.core.name for c in sd.snapshot.characters}
            if character.core.name not in existing_names:
                sd.snapshot.characters.append(character)
            # Re-bind active_scenario on this socket from whatever the
            # peer wrote — its presence on sd.active_scenario is what
            # downstream pressure / accusation code keys off.
            if sd.active_scenario is None:
                bind_result = bind_scenario(
                    sd.genre_pack,
                    sd.snapshot,
                    genre_slug=sd.genre_slug,
                    world_slug=sd.world_slug,
                )
                if bind_result is not None:
                    _, active_pack = bind_result
                    sd.active_scenario = active_pack
            span.add_event(
                "character_creation.mp_world_reused",
                {
                    "event": "mp_world_reused",
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "existing_pc_count": len(existing_chars),
                    "appended_pc": character.core.name,
                    "total_pc_count": len(sd.snapshot.characters),
                    "player_id": player_id,
                },
            )
            logger.info(
                "session.mp_second_commit genre=%s world=%s existing_pcs=%d new_pc=%s total=%d",
                sd.genre_slug,
                sd.world_slug,
                len(existing_chars),
                character.core.name,
                len(sd.snapshot.characters),
            )

        # Lore seeding (Slice F / connect.rs:2196). Must run BEFORE
        # clearing the builder — the seeder reads scene choices to
        # build Character-category lore fragments for narrator RAG.
        lore_added = seed_lore_from_char_creation(sd.lore_store, list(builder.scenes()))
        span.add_event(
            "lore.char_creation_seeded",
            {
                "event": "char_creation_lore_seeded",
                "fragments_added": lore_added,
                "total_fragments": len(sd.lore_store),
                "total_tokens": sd.lore_store.total_tokens(),
                "genre": sd.genre_slug,
                "world": sd.world_slug,
                "player_id": player_id,
            },
        )
        logger.info(
            "rag.character_creation_lore_seeded count=%d total=%d",
            lore_added,
            len(sd.lore_store),
        )
        _watcher_publish(
            "lore_retrieval",
            {
                "reason": "character_creation_seed",
                "fragments_added": lore_added,
                "total_fragments": len(sd.lore_store),
                "total_tokens": sd.lore_store.total_tokens(),
                "genre_slug": sd.genre_slug,
                "world_slug": sd.world_slug,
                "player_id": player_id,
            },
            component="rag",
        )

        # NPC registry reset (Slice G / connect.rs:2136). Drops chargen-
        # tier name extractions (player's own name, lobby filler) so the
        # narrator starts clean. Lore / tropes / regions / history persist.
        # MP: only the first commit clears — second-commit preservation
        # avoids stranding peer-narrated NPCs mid-arc.
        if is_first_commit:
            prev_registry_len = len(sd.snapshot.npc_registry)
            sd.snapshot.npc_registry.clear()
            span.add_event(
                "npc_registry.cleared_on_chargen_complete",
                {
                    "event": "npc_registry.cleared_on_chargen_complete",
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "player": sd.player_name,
                    "previous_len": prev_registry_len,
                    "reason": "fresh_character_narrative_reset",
                },
            )
            logger.info(
                "npc_registry.cleared genre=%s world=%s player=%s prev_len=%d",
                sd.genre_slug,
                sd.world_slug,
                sd.player_name,
                prev_registry_len,
            )

        # MP per-player chargen binding (playtest 2026-04-25). Maps
        # player_id → character_name so slug-resume routes new players
        # to chargen instead of auto-claiming an existing PC.
        if sd.player_id and character.core.name:
            sd.snapshot.player_seats[sd.player_id] = character.core.name
            span.add_event(
                "session.player_seat_bound",
                {
                    "event": "session.player_seat_bound",
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "player_id": sd.player_id,
                    "character_name": character.core.name,
                    "seat_count": len(sd.snapshot.player_seats),
                },
            )
            logger.info(
                "session.player_seat_bound player_id=%s character=%s seat_count=%d",
                sd.player_id,
                character.core.name,
                len(sd.snapshot.player_seats),
            )
            _watcher_publish(
                "session_player_seat_bound",
                {
                    "genre_slug": sd.genre_slug,
                    "world_slug": sd.world_slug,
                    "player_id": sd.player_id,
                    "character_name": character.core.name,
                    "seat_count": len(sd.snapshot.player_seats),
                    "seated_player_ids": list(sd.snapshot.player_seats.keys()),
                },
                component="session",
            )

        # Persist (Slice G / connect.rs:2174). Snapshot save makes the
        # next slug-resume hit has_character=True; failure must not
        # strand the player mid-commit (log loud, continue).
        try:
            # ADR-037 Python port: route through the canonical room save
            # so concurrent chargen-commits from peers are serialized by
            # the room lock. Fallback for legacy non-slug paths.
            if self._room is not None:
                self._room.save()
            else:
                sd.store.save(sd.snapshot)
            span.add_event(
                "session.persisted_at_chargen_complete",
                {
                    "event": "session.persisted",
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "player": sd.player_name,
                    "turn": sd.snapshot.turn_manager.interaction,
                },
            )
            logger.info(
                "session.persisted_at_chargen_complete genre=%s world=%s player=%s",
                sd.genre_slug,
                sd.world_slug,
                sd.player_name,
            )
        except Exception as exc:
            # Don't strand mid-commit — log + OTEL event, then proceed.
            # On next reconnect the save will be absent so chargen repeats.
            logger.error(
                "session.persist_failed_at_chargen_complete genre=%s world=%s error=%s",
                sd.genre_slug,
                sd.world_slug,
                exc,
            )
            span.add_event(
                "session.persist_failed_at_chargen_complete",
                {
                    "event": "session.persist_failed",
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "player": sd.player_name,
                    "error": str(exc),
                },
            )

        # Flip to Playing atomically with persistence (Slice G /
        # connect.rs:2183). Pre-fix, a disconnect between confirm and
        # first action lost the save-state flag.
        self._state = _State.Playing

        sd.builder = None
        # ADR-014 / ADR-078: HP was removed in favor of EdgePool (composure).
        # Log surface-level mechanical state as edge=current/max so playtest
        # logs match the actual schema instead of leaking a stale `hp=N` field.
        # `schema=adr-014` is grep-able so future regressions (re-introduction
        # of an `hp` integer on CreatureCore) are auditable.
        logger.info(
            "chargen.complete schema=adr-014 char_name=%s class=%s race=%s edge=%d/%d",
            character.core.name,
            character.char_class,
            character.race,
            character.core.edge.current,
            character.core.edge.max,
        )

        payload = CharacterCreationPayload(
            phase="complete",
            total_scenes=builder.total_scenes(),
            character=character.model_dump(mode="json"),
        )
        out: list[object] = [CharacterCreationMessage(payload=payload, player_id=player_id)]

        # PARTY_STATUS snapshot (Slice H / connect.rs:2533). Lands the
        # Character tab populated at session-start. MP: also broadcast
        # to peers so they see the new arrival without waiting for their
        # own turn-end refresh.
        try:
            party_status_msg = self._build_session_start_party_status(sd, character, player_id)
            out.append(party_status_msg)
            from sidequest.game.persistence import (  # noqa: PLC0415 — break import cycle
                GameMode as _GameMode,
            )

            if (
                self._room is not None
                and sd.mode == _GameMode.MULTIPLAYER
                and self._socket_id is not None
            ):
                self._room.broadcast(party_status_msg, exclude_socket_id=self._socket_id)
            span.add_event(
                "session.start.character_snapshot_emitted",
                {
                    "event": "session.start.character_snapshot_emitted",
                    "player_id": player_id,
                    "character_name": character.core.name,
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "sheet_class": character.char_class,
                    "inventory_count": len(
                        [
                            i
                            for i in character.core.inventory.items
                            if str(i.get("state", "Carried")) == "Carried"
                        ]
                    ),
                },
            )
        except Exception as exc:
            # Snapshot frame is UI convenience — log loud, don't block.
            logger.error(
                "session.start.character_snapshot_failed player=%s error=%s",
                sd.player_name,
                exc,
            )
            span.add_event(
                "session.start.character_snapshot_failed",
                {
                    "event": "session.start.character_snapshot_failed",
                    "error": str(exc),
                    "player_id": player_id,
                },
            )

        # Opening-turn bootstrap (Slice H / connect.rs:2270). Fires
        # narrator with opening_seed + opening_directive (Early zone),
        # consumed once so subsequent turns run directive-free.
        opening_messages = await self._run_opening_turn_narration(sd, player_id, span)
        out.extend(opening_messages)
        return out

    # ---- helper: next scene message OR confirmation summary -------------
    def _next_message(
        self,
        builder: CharacterBuilder,
        sd: _SessionData,
        player_id: str,
    ) -> list[object]:
        """After apply_choice / apply_freeform / apply_auto_advance, emit the
        appropriate next frame: either the next scene message, or the
        confirmation summary if the builder has transitioned to Confirmation.

        Rust parity: ``dispatch::chargen_summary::render_confirmation_summary``
        takes the pack + lobby_name directly because the builder does not
        own them.
        """
        if builder.is_confirmation():
            return [render_confirmation_summary(builder, sd.genre_pack, sd.player_name, player_id)]
        return [builder.to_scene_message(player_id)]

    # ------------------------------------------------------------------
    # PLAYER_ACTION dispatch
    # ------------------------------------------------------------------

    async def _handle_player_action(self, msg: GameMessage) -> list[object]:
        if self._state not in (_State.Creating, _State.Playing):
            return [_error_msg("Cannot process PLAYER_ACTION: not connected")]

        if self._session_data is None:
            return [_error_msg("Internal error: session data missing")]

        payload = msg.payload  # type: ignore[attr-defined]
        raw_action: str = str(payload.action)

        # Sanitize player input
        action = sanitize_player_text(raw_action)
        if not action:
            return [_error_msg("Player action is empty after sanitization")]

        # Story 3.4 Task 12: strip [combat] markers from aside-flagged actions
        # before they reach the orchestrator (port of dispatch/aside.rs).
        if getattr(payload, "aside", False):
            from sidequest.server.dispatch.combat_brackets import (
                strip_combat_brackets,
            )

            action = strip_combat_brackets(action)
            if not action:
                return [_error_msg("Player aside is empty after combat-bracket strip")]

        logger.info(
            "session.player_action genre=%s world=%s player=%s action_len=%d",
            self._session_data.genre_slug,
            self._session_data.world_slug,
            self._session_data.player_name,
            len(action),
        )

        # Pause gate (MP-02 Task 6): if any seated player is absent, return
        # GAME_PAUSED and do NOT dispatch to the narrator. This must run
        # BEFORE _execute_narration_turn so the monkeypatch gate in tests
        # confirms the method is never reached when paused. _room is None
        # when slug-connect hasn't bound a room (legacy connect path or
        # pre-connect test paths) — pause gate is a no-op in that case.
        if self._room is not None and self._room.is_paused():
            from sidequest.telemetry.spans import mp_player_action_paused_span

            absent = self._room.absent_seated_player_ids()
            player_id_attr = self._session_data.player_id if self._session_data else ""
            with mp_player_action_paused_span(
                slug=self._room.slug,
                player_id=player_id_attr,
                absent_player_ids=absent,
            ):
                logger.info(
                    "session.player_action_blocked_paused absent=%s slug=%s",
                    absent,
                    self._room.slug,
                )
            return [GamePausedMessage(payload=GamePausedPayload(waiting_for=absent))]

        # Transition to Playing on first action (handles chargen via narration)
        if self._state == _State.Creating:
            self._state = _State.Playing

        sd = self._session_data
        # MP turn-ownership signal (ADR-036 sealed-letter pacing). Broadcast
        # TURN_STATUS{status="active"} to every socket in the room so peers
        # can flip MultiplayerTurnBanner to tone="peer" while this player's
        # narration runs. Without this signal, peer tabs stayed on tone="you"
        # and gave no indication that another player was acting (playtest
        # 2026-04-25 "No peer-turn signal"). exclude_socket_id=None — the
        # actor receives it too; their banner already prefers "thinking" over
        # "you" while ``thinking=true`` is local.
        if self._room is not None and sd.player_name:
            try:
                acting_name = _resolve_acting_character_name(sd, self._room)
                turn_active_msg = TurnStatusMessage(
                    payload=TurnStatusPayload(
                        player_name=NonBlankString(acting_name),
                        status="active",
                    ),
                    player_id=sd.player_id or "",
                )
                self._room.broadcast(turn_active_msg, exclude_socket_id=None)
                logger.info(
                    "session.turn_status_active player=%s player_id=%s slug=%s",
                    acting_name,
                    sd.player_id,
                    self._room.slug,
                )
                _watcher_publish(
                    "turn_status",
                    {
                        "status": "active",
                        "player_name": acting_name,
                        "player_id": sd.player_id,
                        "slug": self._room.slug,
                    },
                    component="session",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("session.turn_status_active_broadcast_failed error=%s", exc)

        lore_context = await self._retrieve_lore_for_turn(sd, action)
        turn_context = _build_turn_context(sd, lore_context=lore_context, room=self._room)
        return await self._execute_narration_turn(sd, action, turn_context)

    # ------------------------------------------------------------------
    # Narration execution — shared between player_action and opening turn
    # ------------------------------------------------------------------

    async def _execute_narration_turn(
        self,
        sd: _SessionData,
        action: str,
        turn_context: TurnContext,
    ) -> list[object]:
        """Run one narration turn: orchestrator call, snapshot mutation,
        persistence, NARRATION + NARRATION_END message build.

        Shared by :meth:`_handle_player_action` and
        :meth:`_run_opening_turn_narration` (Story 2.3 Slice H). The
        caller owns TurnContext construction so each entrypoint can
        set per-turn fields (opening_directive on turn 0, pending
        trope beats on subsequent turns) without leaking responsibility.
        """
        snapshot = sd.snapshot
        snapshot_before_hash = _hash_snapshot(snapshot)
        with turn_span(
            turn_id=snapshot.turn_manager.interaction,
            player_id=sd.player_id,
            agent_name="narrator",
            genre=sd.genre_slug,
            world=sd.world_slug,
            action_len=len(action),
        ):
            # Group B — Local DM decomposer runs between sealed-letter and narrator.
            # LocalDM.decompose catches expected client failures internally and
            # returns a degraded DispatchPackage. Any exception escaping here is a
            # programmer bug (rename, signature drift); let it propagate — failing
            # the turn loudly beats silently demoting bugs to degraded.
            turn_id = (
                f"{sd.genre_slug}:{sd.world_slug}:{sd.player_id}:"
                f"{snapshot.turn_manager.interaction}"
            )
            assert turn_context.state_summary is not None, (
                "TurnContext.state_summary must be populated by _build_turn_context"
            )
            dispatch_package = await sd.local_dm.decompose(
                turn_id=turn_id,
                player_id=f"player:{sd.player_name}",
                raw_action=action,
                state_summary=turn_context.state_summary,
                visibility_baseline=sd.genre_pack.visibility_baseline,
            )
            if dispatch_package.degraded:
                logger.info(
                    "session.decomposer_degraded reason=%s turn_id=%s",
                    dispatch_package.degraded_reason,
                    turn_id,
                )
                # Surface to GM panel — per CLAUDE.md OTEL principle, every
                # subsystem decision must be visible. Decomposer degradation
                # silently strips per-player narrator instructions (which
                # ADR-028/036 multiplayer isolation depends on); without
                # this event the lie detector can't tell the dispatcher
                # ran on a degraded package.
                _watcher_publish(
                    "decomposer_degraded",
                    {
                        "turn_id": turn_id,
                        "reason": dispatch_package.degraded_reason or "",
                        "player_id": f"player:{sd.player_name}",
                    },
                    component="local_dm",
                    severity="warning",
                )
            turn_context.dispatch_package = dispatch_package

            with orchestrator_process_action_span(action_len=len(action)):
                result = await sd.orchestrator.run_narration_turn(action, turn_context)

            logger.info(
                "session.narration_complete genre=%s world=%s degraded=%s duration_ms=%s",
                sd.genre_slug,
                sd.world_slug,
                result.is_degraded,
                result.agent_duration_ms,
            )

            # Capture encounter state BEFORE applying the narration result so we
            # can detect transitions (live→resolved) after the dispatch below and
            # emit the corresponding state_transition / resolve events.
            prior_encounter = snapshot.encounter
            prior_live = prior_encounter is not None and not prior_encounter.resolved
            prior_type = prior_encounter.encounter_type if prior_encounter else None

            # Unified dispatch — passes the pack so encounter instantiation /
            # beat application / resolution happen in one place (emits the
            # Story-3.4 OTEL spans the GM panel reads).
            #
            # ADR-074 dice integration — read the most recent dice outcome
            # stashed by the DICE_THROW handler (if any) and classify it as
            # success/failure for the beat application. Uses getattr so this
            # stays forward-compatible with the in-flight ``pending_roll_outcome``
            # field on ``_SessionData`` that OQ-2 is landing in parallel —
            # when the field is absent the call is a no-op.
            dice_outcome = getattr(sd, "pending_roll_outcome", None)
            dice_failed: bool | None = None
            if dice_outcome is not None:
                outcome_name = getattr(dice_outcome, "name", None) or str(dice_outcome)
                dice_failed = outcome_name in ("Fail", "CritFail")
            dice_actor: str | None = getattr(sd, "pending_roll_actor", None)
            _apply_narration_result_to_snapshot(
                snapshot,
                result,
                sd.player_name,
                pack=sd.genre_pack,
                dice_failed=dice_failed,
                dice_actor=dice_actor,
            )
            # Consume the pending outcome — one turn per roll.
            if dice_outcome is not None and hasattr(sd, "pending_roll_outcome"):
                sd.pending_roll_outcome = None
            if hasattr(sd, "pending_roll_actor"):
                sd.pending_roll_actor = None
            snapshot.turn_manager.record_interaction()

            now_encounter = snapshot.encounter
            now_live = now_encounter is not None and not now_encounter.resolved

            from sidequest.server.dispatch.encounter_lifecycle import (
                _is_combat_category,
                apply_resource_patches,
                award_turn_xp,
            )

            in_combat_now = (
                snapshot.encounter is not None
                and not snapshot.encounter.resolved
                and _is_combat_category(sd.genre_pack, snapshot.encounter.encounter_type)
            )
            award_turn_xp(snapshot, in_combat=in_combat_now)

            try:
                crossed_thresholds = apply_resource_patches(
                    snapshot,
                    affinity_progress=result.affinity_progress or [],
                    lore_store=sd.lore_store,
                    turn=snapshot.turn_manager.interaction,
                )
            except Exception as exc:  # noqa: BLE001 — LLM typos must not kill the turn
                logger.warning(
                    "resource.patch_failed error=%s — skipping threshold mint for this turn",
                    exc,
                )
                crossed_thresholds = []
            for t in crossed_thresholds:
                logger.info(
                    "resource.threshold_crossed event_id=%s at=%s",
                    t.event_id,
                    t.at,
                )

            try:
                # ADR-037 Python port: room owns the canonical snapshot, so a
                # plain room.save() is sufficient — there is no per-session
                # divergence to merge. Falls back to sd.store.save when the
                # legacy non-slug path didn't bind a room.
                if self._room is not None:
                    self._room.save()
                else:
                    sd.store.save(snapshot)
                narrative_entry = NarrativeEntry(
                    timestamp=0,
                    round=snapshot.turn_manager.interaction,
                    author="narrator",
                    content=result.narration,
                    tags=[],
                )
                sd.store.append_narrative(narrative_entry)
                logger.info(
                    "session.persisted turn=%d player=%s char_count=%d seat_count=%d",
                    snapshot.turn_manager.interaction,
                    sd.player_name,
                    len(snapshot.characters),
                    len(snapshot.player_seats),
                )
            except Exception as exc:
                logger.error("session.persist_failed error=%s", exc)

            # Story 37-33: embed newly-seeded / pending lore fragments in the
            # background so the *next* turn's RAG retrieval can find them.
            # Spawns a fire-and-forget task — the narration turn returns to
            # the player immediately; embeds populate during the human's
            # reading time.
            self._dispatch_embed_worker(sd)

            narration_text = result.narration or "(The world holds its breath...)"
            try:
                narration_nbs = NonBlankString(narration_text)
            except Exception:
                narration_nbs = NonBlankString("The world holds its breath...")

            # Forward extracted footnotes into the NarrationPayload so the UI
            # Knowledge journal fills. Narrator produces them every turn
            # (see `game_patch.extracted footnotes=N` in the server log) and
            # the UI's useStateMirror was already wired to consume them — the
            # session handler was the only missing link. Coerce raw dicts
            # from the extraction into typed Footnote models, skipping any
            # that fail validation rather than crashing the turn.
            forwarded_footnotes: list[Footnote] = []
            for fn in result.footnotes or []:
                if not isinstance(fn, dict):
                    continue
                try:
                    forwarded_footnotes.append(Footnote(**fn))
                except Exception as exc:  # noqa: BLE001 — drop-and-log is safer than a mid-turn crash
                    logger.warning(
                        "state.footnote_coerce_failed error=%s payload=%r",
                        exc,
                        fn,
                    )
            logger.info(
                "state.footnotes_forwarded count=%d player=%s",
                len(forwarded_footnotes),
                sd.player_name,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "footnotes",
                    "count": len(forwarded_footnotes),
                    "player_id": sd.player_id,
                    "turn_number": snapshot.turn_manager.interaction,
                    "summaries": [fn.summary for fn in forwarded_footnotes][:10],
                },
                component="footnotes",
            )
            narration_payload = NarrationPayload(
                text=narration_nbs,
                state_delta=None,
                footnotes=forwarded_footnotes,
                visibility_sidecar=aggregate_visibility(dispatch_package),
            )
            # MP-03 Task 3: route through EventLog + ProjectionFilter before send.
            narration_msg = self._emit_event("NARRATION", narration_payload)

            # Pingpong 2026-04-26 [S3-REGRESSION]: emit a SCRAPBOOK_ENTRY for
            # every narration turn so the UI gallery has metadata to merge with
            # the IMAGE that lands later from the daemon. Pure reuse — no new
            # LLM calls. Fields come from the orchestrator result and the
            # snapshot the narrator just stamped.
            try:
                self._emit_scrapbook_entry(
                    sd=sd,
                    snapshot=snapshot,
                    result=result,
                )
            except Exception as exc:  # noqa: BLE001 — scrapbook must never crash a turn
                logger.warning(
                    "scrapbook.emit_failed turn=%d error=%s",
                    snapshot.turn_manager.interaction,
                    exc,
                )

            # Group G Task 6: route prompt-redacted dispatches as SECRET_NOTE
            # events. Task 5's ``redact_dispatch_package`` stripped these from the
            # narrator prompt and parked them on ``result.secret_routes``; here we
            # reify each one as its own event so the same ProjectionFilter /
            # visibility_tag rule (Task 3) delivers it only to the recipients in
            # its ``_visibility.visible_to``. Only SubsystemDispatch entries route;
            # see ``build_secret_note_events`` for the skip rules.
            for _envelope in build_secret_note_events(
                result.secret_routes,
                turn_id=dispatch_package.turn_id,
            ):
                import json as _json

                _payload_data = _json.loads(_envelope.payload_json)
                self._emit_event(
                    "SECRET_NOTE",
                    SecretNotePayload(
                        turn_id=_payload_data["turn_id"],
                        idempotency_key=_payload_data["idempotency_key"],
                        subsystem=_payload_data["subsystem"],
                        params=_payload_data.get("params", {}),
                        visibility_sidecar=_payload_data["_visibility"],
                    ),
                )

            # Story 3.4 Task 11: emit CONFRONTATION when encounter state transitions.
            # OTEL visibility: add event to current span so the GM panel (Sebastien-
            # tier mechanical visibility) can see the dispatch decision end-to-end.
            # Pingpong 2026-04-26 S2-BUG: confrontations were PRIVATE to the
            # acting player — peers froze on the prior shared beat (no NPC
            # card, no narration, no buttons). Root cause: the message was
            # built directly via ``ConfrontationMessage(...)`` and only
            # appended to the actor's ``outbound`` list, never broadcast.
            # Fix: route through ``self._emit_event("CONFRONTATION", ...)``
            # so the canonical EventLog + ProjectionFilter fan-out path
            # delivers the same per-player frame to every connected peer
            # (mirrors how NARRATION is emitted at the line above). The
            # kind is already registered in ``_KIND_TO_MESSAGE_CLS``.
            # Independent of the multi-target parse-failure (#5) — that
            # lives in local_dm and degrades the dispatch package, but
            # the missing broadcast here is the sole cause of peer freeze.
            confrontation_msg: object | None = None
            confrontation_payload: ConfrontationPayload | None = None
            confrontation_event_attrs: dict[str, object] | None = None
            if now_live and now_encounter is not None:
                from sidequest.server.dispatch.confrontation import (
                    build_confrontation_payload,
                    find_confrontation_def,
                )

                cdef = find_confrontation_def(
                    sd.genre_pack.rules.confrontations if sd.genre_pack.rules else [],
                    now_encounter.encounter_type,
                )
                # No silent fallback: an active encounter whose type is not in the
                # pack is a pack-data bug. Task 10 raises in the same case during
                # beat-apply; the dispatch path matches.
                if cdef is None:
                    raise ValueError(
                        f"active encounter type {now_encounter.encounter_type!r} "
                        f"not in pack confrontations (genre={sd.genre_slug!r})"
                    )
                payload_dict = build_confrontation_payload(
                    encounter=now_encounter,
                    cdef=cdef,
                    genre_slug=sd.genre_slug,
                )
                confrontation_payload = ConfrontationPayload(**payload_dict)
                confrontation_event_attrs = {
                    "active": True,
                    "encounter_type": now_encounter.encounter_type,
                    "genre_slug": sd.genre_slug,
                }
            elif prior_live and not now_live:
                from sidequest.server.dispatch.confrontation import (
                    build_clear_confrontation_payload,
                )

                assert prior_type is not None  # guaranteed by prior_live=True
                payload_dict = build_clear_confrontation_payload(
                    encounter_type=prior_type,
                    genre_slug=sd.genre_slug,
                )
                confrontation_payload = ConfrontationPayload(**payload_dict)
                confrontation_event_attrs = {
                    "active": False,
                    "encounter_type": prior_type,
                    "genre_slug": sd.genre_slug,
                }

            if confrontation_payload is not None:
                confrontation_msg = self._emit_event(
                    "CONFRONTATION",
                    confrontation_payload,
                )
                assert confrontation_event_attrs is not None
                trace.get_current_span().add_event(
                    "confrontation.dispatched",
                    confrontation_event_attrs,
                )
                # OTEL lie-detector hook (per CLAUDE.md OTEL principle): the
                # GM panel needs evidence the broadcast actually reached
                # peers — not just that the actor saw a confrontation card.
                # Without this, a regression to the pre-fix behavior (frame
                # appended only to actor's outbound) is invisible to the
                # watcher dashboard.
                peer_player_ids: list[str] = []
                room_slug: str = ""
                if self._room is not None:
                    # Some unit-test fixtures (e.g. _StubRoom in
                    # test_dice_throw_wiring) provide a minimal Room shim
                    # that lacks ``connected_player_ids`` / ``slug``. The
                    # OTEL hook is best-effort logging — never crash the
                    # turn for missing observability metadata.
                    import contextlib  # noqa: PLC0415 — local import keeps hot path lean

                    with contextlib.suppress(AttributeError):
                        peer_player_ids = [
                            pid for pid in self._room.connected_player_ids() if pid != sd.player_id
                        ]
                    room_slug = getattr(self._room, "slug", "") or ""
                logger.info(
                    "confrontation.peer_projection_broadcast slug=%s acting=%s "
                    "encounter_type=%s active=%s peers=%s",
                    room_slug,
                    sd.player_id,
                    confrontation_event_attrs["encounter_type"],
                    confrontation_event_attrs["active"],
                    peer_player_ids,
                )
                _watcher_publish(
                    "confrontation_peer_projection_broadcast",
                    {
                        "slug": room_slug,
                        "acting_player_id": sd.player_id,
                        "encounter_type": confrontation_event_attrs["encounter_type"],
                        "active": confrontation_event_attrs["active"],
                        "peers": peer_player_ids,
                    },
                    component="confrontation",
                )

            outbound: list[object] = [narration_msg]
            if confrontation_msg is not None:
                outbound.append(confrontation_msg)
            # CHAPTER_MARKER — the UI's ``useRunningHeader`` hook derives the
            # running-header chapter title from this frame. When the narrator
            # emits a location in game_patch, the new location is already on
            # ``snapshot.location`` (applied in
            # ``_apply_narration_result_to_snapshot``). Emit one frame per
            # location change so the header updates in lock-step with
            # narration. Without this the header stays blank since the UI
            # never saw the server's ``state.location_update`` log line.
            # Pingpong 2026-04-24 — "Location not rendered in the header on
            # resume" — fix is symmetric (slug-resume bootstrap also emits
            # CHAPTER_MARKER; see the slug-connect block).
            if result.location:
                outbound.append(
                    ChapterMarkerMessage(
                        payload=ChapterMarkerPayload(
                            title=None,
                            location=_resolve_location_display(
                                sd.genre_pack, sd.world_slug, snapshot.location
                            ),
                        ),
                        player_id=sd.player_id,
                    ),
                )
            outbound.append(
                NarrationEndMessage(
                    type="NARRATION_END",  # type: ignore[arg-type]
                    payload=NarrationEndPayload(state_delta=None),
                    player_id=sd.player_id,
                ),
            )

            # MP turn-ownership clear (ADR-036 sealed-letter pacing). Pair with
            # the TURN_STATUS{active} broadcast at action receipt — peers' banner
            # tone="peer" stays stuck without this clear. exclude_socket_id=None
            # so every socket (including the actor) gets the resolved signal;
            # the UI clears activePlayerName on status="resolved".
            if self._room is not None and sd.player_name:
                try:
                    acting_name = _resolve_acting_character_name(sd, self._room)
                    turn_resolved_msg = TurnStatusMessage(
                        payload=TurnStatusPayload(
                            player_name=NonBlankString(acting_name),
                            status="resolved",
                        ),
                        player_id=sd.player_id or "",
                    )
                    self._room.broadcast(turn_resolved_msg, exclude_socket_id=None)
                    logger.info(
                        "session.turn_status_resolved player=%s player_id=%s slug=%s",
                        acting_name,
                        sd.player_id,
                        self._room.slug,
                    )
                    _watcher_publish(
                        "turn_status",
                        {
                            "status": "resolved",
                            "player_name": acting_name,
                            "player_id": sd.player_id,
                            "slug": self._room.slug,
                        },
                        component="session",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "session.turn_status_resolved_broadcast_failed error=%s",
                        exc,
                    )

            # Refresh PARTY_STATUS so `current_location` and any HP/inventory
            # mutations landed by the narration apply propagate to the client
            # header / CharacterSheet / MapOverlay. Previously PARTY_STATUS
            # was emitted exactly once at chargen-end (before the opening
            # turn), which froze the location at its pre-opening value
            # (typically empty). Playtest 2026-04-22.
            if snapshot.characters:
                try:
                    # MP: resolve "self" by sd.player_id, not snapshot.characters[0].
                    # The turn-end refresh fires per-socket; if the requesting
                    # socket isn't characters[0] (any non-first-committed
                    # player in MP), passing characters[0] mis-tags that PC's
                    # data with the requesting socket's player_id and the UI
                    # renders the wrong PC as "(YOU)" — playtest 2026-04-25
                    # "Tab 2 sees Laverne (YOU)".
                    self_char = self._resolve_self_character(sd) or snapshot.characters[0]
                    party_status = self._build_session_start_party_status(
                        sd, self_char, sd.player_id
                    )
                    outbound.append(party_status)
                    logger.info(
                        "state.party_status_emitted reason=turn_end location=%r turn=%d "
                        "self_char=%s",
                        snapshot.location or "",
                        snapshot.turn_manager.interaction,
                        self_char.core.name,
                    )
                    _watcher_publish(
                        "state_transition",
                        {
                            "field": "party_status",
                            "reason": "turn_end",
                            "location": snapshot.location or "",
                            "turn_number": snapshot.turn_manager.interaction,
                            "player_id": sd.player_id,
                        },
                        component="party_status",
                    )
                except Exception as exc:  # noqa: BLE001 — party refresh must never crash a turn
                    logger.warning("state.party_status_refresh_failed error=%s", exc)

            # Visual-scene render dispatch. Fire-and-forget: the RENDER_QUEUED
            # message ships with the NARRATION payload; the async render task
            # posts an IMAGE message onto the per-connection outbound queue
            # when the daemon replies. Short-circuits without any socket work
            # when: render flag off, no visual scene, daemon socket missing,
            # or outbound queue unavailable (test configurations that don't
            # attach room context).
            render_queued = self._maybe_dispatch_render(sd, result)
            if render_queued is not None:
                outbound.append(render_queued)

            # Audio DJ dispatch. Synchronous: AUDIO_CUE (or nothing) ships
            # with this turn's outbound frames. No placeholder + later message
            # dance — the DJ is a local filesystem lookup.
            audio_cue = self._maybe_dispatch_audio(sd, result)
            if audio_cue is not None:
                outbound.append(audio_cue)

            # turn_complete is now emitted by the validator (per ADR-089 §6.7).
            # The TurnRecord assembled below is the single source of truth.

            # --- TurnRecord assembly + validator submit ---
            # Wrapped in try/except: the validator must NEVER crash the hot path.
            if self._validator is not None:
                try:
                    _patch_summaries: list[PatchSummary] = []
                    if result.location:
                        _patch_summaries.append(
                            PatchSummary(patch_type="location", fields_changed=["location"])
                        )
                    if result.quest_updates:
                        _patch_summaries.append(
                            PatchSummary(
                                patch_type="quest", fields_changed=list(result.quest_updates)
                            )
                        )
                    if result.lore_established:
                        _patch_summaries.append(
                            PatchSummary(patch_type="lore", fields_changed=["lore_established"])
                        )
                    if result.npcs_present:
                        _patch_summaries.append(
                            PatchSummary(
                                patch_type="npc_registry",
                                fields_changed=[n.name for n in result.npcs_present],
                            )
                        )
                    if result.items_gained or result.items_lost:
                        _patch_summaries.append(
                            PatchSummary(patch_type="inventory", fields_changed=[])
                        )

                    _beats_fired: list[tuple[str, float]] = []
                    for beat in result.beat_selections or []:
                        _beats_fired.append(
                            (
                                getattr(beat, "trope_id", None)
                                or getattr(beat, "beat_id", None)
                                or "unknown",
                                float(getattr(beat, "threshold", 0.0) or 0.0),
                            )
                        )

                    record = TurnRecord(
                        turn_id=snapshot.turn_manager.interaction,
                        timestamp=datetime.now(UTC),
                        player_id=sd.player_id,
                        player_input=action,
                        classified_intent="unknown",  # TODO: tighten when LocalDM exposes intent
                        agent_name=result.agent_name or "narrator",
                        narration=result.narration or "",
                        patches_applied=_patch_summaries,
                        snapshot_before_hash=snapshot_before_hash,
                        snapshot_after=snapshot,
                        delta=None,  # TODO: tighten when StateDelta is wired
                        beats_fired=_beats_fired,
                        extraction_tier=1,  # TODO: map result.prompt_tier to int tier
                        token_count_in=result.token_count_in or 0,
                        token_count_out=result.token_count_out or 0,
                        agent_duration_ms=result.agent_duration_ms or 0,
                        is_degraded=result.is_degraded,
                    )
                    await self._validator.submit(record)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("turn_record.assemble_failed: %s", exc)

            return outbound

    async def _run_opening_turn_narration(
        self,
        sd: _SessionData,
        player_id: str,
        span: trace.Span,
    ) -> list[object]:
        """Fire the opening narration turn at the end of chargen.

        Consumes ``sd.opening_seed`` + ``sd.opening_directive`` exactly
        once. The seed becomes the first "action" string; the directive
        is injected into the narrator's Early zone for this turn only.
        Both fields are zeroed on the session after the turn runs so
        the next PLAYER_ACTION turn sees a fresh context.

        When no opening hook was resolved at connect time (pack has no
        openings) the Rust dispatcher substitutes a generic
        "I look around and take in my surroundings." Match that — the
        narrator still fires so the player lands in the world rather
        than at a blank UI.

        Rust parity: connect.rs:2270-2529.
        """
        # Consume-time MP-joiner suppression (playtest 2026-04-26
        # [S2-BUG] coyote_reach regression). The connect-time guard in
        # ``_handle_connect`` only fires when the joiner connects AFTER
        # the host has completed chargen — checking
        # ``len(snapshot.characters) > 0`` at connect-time. In the more
        # common race scenario (both players in lobby together, both
        # walking chargen at the same time) the joiner's ``sd.opening_
        # seed/directive`` get populated at connect because the snapshot
        # was empty, and only the timing of chargen-completion decides
        # who's first vs. second. This guard catches the second
        # committer at consume-time: by the time we get here, the joiner's
        # PC is already in ``sd.snapshot.characters`` (appended in the
        # second-commit branch around line 2725), so the test is "more
        # than just me" → at least one peer character is present →
        # suppress the cold-open and fall back to the generic continuation
        # action so the persistent narrator (ADR-067) treats this as
        # scene continuation, not a fresh in-medias-res open.
        if sd.opening_seed is not None and len(sd.snapshot.characters) > 1:
            _watcher_publish(
                "mp_joiner_opening_suppressed_at_consume",
                {
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "player_id": player_id,
                    "player_name": sd.player_name,
                    "character_count": len(sd.snapshot.characters),
                    "had_seed": True,
                    "had_directive": sd.opening_directive is not None,
                },
                component="opening_hook",
                severity="info",
            )
            logger.info(
                "session.mp_joiner_opening_suppressed_at_consume "
                "genre=%s world=%s player=%s character_count=%d",
                sd.genre_slug,
                sd.world_slug,
                sd.player_name,
                len(sd.snapshot.characters),
            )
            span.add_event(
                "mp_joiner_opening_suppressed_at_consume",
                {
                    "event": "mp_joiner_opening_suppressed_at_consume",
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "player_id": player_id,
                    "character_count": len(sd.snapshot.characters),
                },
            )
            sd.opening_seed = None
            sd.opening_directive = None

        action = sd.opening_seed or "I look around and take in my surroundings."
        source_tier = "world_or_genre_hook" if sd.opening_seed else "fallback"

        # Cold-open delivery (playtest 2026-04-25 [P2]). The opening seed
        # is in-medias-res prose the world author wrote for the player to
        # READ — not narrator prompt-context. Previously it was passed only
        # as `action='...'` and the narrator silently consumed it: long
        # hooks got truncated (player saw "the iron door grinds inward" but
        # never the kidnapping setup), short hooks survived expansion by
        # accident. The contract was broken either way.
        #
        # Fix: emit the seed directly to the player as a NARRATION message
        # BEFORE running the narrator. The narrator's first turn still
        # receives the seed as `action` and continues from where the hook
        # ends — so what the player sees is hook + continuation as a
        # single coherent opening beat, instead of the hook being a ghost
        # in the prompt. Suppressed when the pack has no opening hook
        # (the fallback "I look around…" is the player's implicit action,
        # not authored cold-open prose).
        cold_open_messages: list[object] = []
        if sd.opening_seed:
            cold_open_messages.append(
                NarrationMessage(
                    payload=NarrationPayload(text=NonBlankString(sd.opening_seed)),
                )
            )
            _watcher_publish(
                "cold_open_emitted",
                {
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "seed_len": len(sd.opening_seed),
                },
                component="opening_hook",
                severity="info",
            )

        lore_context = await self._retrieve_lore_for_turn(sd, action)
        turn_context = _build_turn_context(
            sd,
            opening_directive=sd.opening_directive,
            lore_context=lore_context,
            room=self._room,
        )

        span.add_event(
            "opening_turn.dispatched",
            {
                "event": "opening_turn.dispatched",
                "has_directive": sd.opening_directive is not None,
                "seed_source": source_tier,
                "action_len": len(action),
                "genre": sd.genre_slug,
                "world": sd.world_slug,
                "cold_open_emitted": bool(cold_open_messages),
            },
        )

        narrator_messages = await self._execute_narration_turn(sd, action, turn_context)
        messages = cold_open_messages + list(narrator_messages)

        # Consume once — Rust uses `opening_directive.take()`; subsequent
        # turns must run directive-free. Same for the seed: it's a
        # one-shot bootstrap action, not a recurring input.
        sd.opening_seed = None
        sd.opening_directive = None

        return messages

    # ------------------------------------------------------------------
    # Audio DJ backend construction
    # ------------------------------------------------------------------

    def _build_audio_backend(
        self,
        genre_slug: str,
        genre_pack: GenrePack,
    ) -> LibraryBackend | None:
        """Construct the per-session LibraryBackend, or None when the
        genre pack has no resolvable on-disk audio directory.

        Emits a watcher event when audio is disabled so the GM panel
        can tell whether a silent turn is because the narration had
        no cues or because audio is off entirely."""
        try:
            pack_dir = GenreLoader().find(genre_slug)
        except Exception as exc:  # noqa: BLE001 — best-effort; never crash connect
            # Span emission replaces the prior direct ``_watcher_publish`` —
            # ``WatcherSpanProcessor`` re-emits via
            # ``SPAN_ROUTES[SPAN_AUDIO_BACKEND_DISABLED]``.
            with audio_backend_disabled_span(
                reason="pack_dir_missing",
                genre=genre_slug,
            ):
                logger.warning(
                    "audio.backend_skipped reason=pack_dir_missing genre=%s error=%s",
                    genre_slug,
                    exc,
                )
            return None

        audio_cfg = genre_pack.audio
        if not audio_cfg.mood_tracks and not audio_cfg.themes and not audio_cfg.sfx_library:
            with audio_backend_disabled_span(
                reason="empty_config",
                genre=genre_slug,
            ):
                logger.info(
                    "audio.backend_skipped reason=empty_config genre=%s",
                    genre_slug,
                )
            return None

        with audio_backend_enabled_span(
            genre=genre_slug,
            mood_count=len(audio_cfg.mood_tracks) + len(audio_cfg.themes),
            sfx_count=len(audio_cfg.sfx_library),
        ):
            logger.info(
                "audio.backend_ready genre=%s pack_dir=%s",
                genre_slug,
                pack_dir,
            )
        return LibraryBackend(audio_cfg, base_path=pack_dir)

    # ------------------------------------------------------------------
    # Visual-scene render dispatch
    # ------------------------------------------------------------------

    def _maybe_dispatch_render(
        self,
        sd: _SessionData,
        result: object,
    ) -> RenderQueuedMessage | None:
        """Fire a render request at the media daemon if the narrator flagged
        a visual scene and the pipeline is enabled.

        Returns a ``RenderQueuedMessage`` to append to the turn's outbound
        frames, or ``None`` when nothing was dispatched (no scene, feature
        flag off, daemon offline, or no outbound queue).

        The actual daemon round-trip runs on a background task; the IMAGE
        reply lands on ``self._out_queue`` whenever the render completes.
        Failures are swallowed with OTEL spans + watcher events so that no
        render error ever crashes a turn.
        """
        from sidequest.agents.orchestrator import NarrationTurnResult

        if not isinstance(result, NarrationTurnResult):
            return None
        visual = result.visual_scene
        if visual is None or not getattr(visual, "subject", "").strip():
            _watcher_publish(
                "state_transition",
                {
                    "field": "render",
                    "op": "skipped",
                    "reason": "no_visual_scene",
                    "turn_number": sd.snapshot.turn_manager.interaction,
                },
                component="render",
            )
            return None
        if not render_enabled():
            logger.info("render.skipped reason=feature_flag_disabled")
            _watcher_publish(
                "state_transition",
                {
                    "field": "render",
                    "op": "skipped",
                    "reason": "feature_flag_disabled",
                    "turn_number": sd.snapshot.turn_manager.interaction,
                },
                component="render",
            )
            return None
        client = DaemonClient()
        if not client.is_available():
            logger.warning(
                "render.skipped reason=daemon_unavailable socket=%s",
                client.socket_path,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "render",
                    "op": "skipped",
                    "reason": "daemon_unavailable",
                    "socket": str(client.socket_path),
                    "turn_number": sd.snapshot.turn_manager.interaction,
                },
                component="render",
                severity="warning",
            )
            return None
        if self._out_queue is None:
            # Test configurations that don't attach room context can't
            # receive async IMAGE frames. Skip loudly so we don't fire
            # a render whose result has nowhere to land.
            logger.warning("render.skipped reason=no_outbound_queue")
            return None

        # ADR-050 image pacing throttle. Consult BEFORE allocating a
        # render_id or touching the daemon — suppressed renders should
        # leave no trace beyond the OTEL decision event.
        throttle_decision = sd.image_pacing_throttle.should_render()
        provisional_render_id = uuid.uuid4().hex[:12]
        if not throttle_decision.allowed:
            logger.info(
                "render.throttled render_id=%s reason=%s remaining=%ds",
                provisional_render_id,
                throttle_decision.reason,
                throttle_decision.cooldown_remaining_seconds,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "render",
                    "op": "throttle_decision",
                    "decision": "suppress",
                    "reason": throttle_decision.reason,
                    "render_id": provisional_render_id,
                    "cooldown_remaining_seconds": (throttle_decision.cooldown_remaining_seconds),
                    "cooldown_seconds": sd.image_pacing_throttle.cooldown_seconds,
                    "turn_number": sd.snapshot.turn_manager.interaction,
                },
                component="render",
            )
            return None
        # Allowed — emit the allow decision so the GM panel can see both
        # branches in the OTEL stream (lie-detector requirement per
        # CLAUDE.md OTEL Observability Principle).
        _watcher_publish(
            "state_transition",
            {
                "field": "render",
                "op": "throttle_decision",
                "decision": "allow",
                "reason": throttle_decision.reason,
                "render_id": provisional_render_id,
                "cooldown_seconds": sd.image_pacing_throttle.cooldown_seconds,
                "turn_number": sd.snapshot.turn_manager.interaction,
            },
            component="render",
        )

        render_id = provisional_render_id
        tier = (visual.tier or "scene_illustration").strip() or "scene_illustration"
        params: dict[str, object] = {
            "tier": tier,
            "subject": visual.subject,
            "mood": visual.mood or "",
            "tags": list(visual.tags or []),
            "location": sd.snapshot.location or "",
            "narration": result.narration,
            "genre": sd.genre_slug,
        }
        # Portrait initials overlay (story 37-30 AC-4): the daemon's
        # portrait composer needs the character's display name to draw
        # the initials card. Other tiers ignore the field.
        if tier == "portrait":
            params["subject_name"] = sd.player_name

        # Story 37-30 — record the (room_slug, player_id) mapping at
        # dispatch so the completion handler can route the IMAGE through
        # the live RoomRegistry queue instead of a closure-captured one
        # that may have gone stale across a reconnect.
        room_slug = self._room.slug if self._room is not None else None
        player_id = sd.player_id

        logger.info(
            "render.dispatched render_id=%s tier=%s subject=%r",
            render_id,
            tier,
            visual.subject[:80],
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "render",
                "op": "dispatched",
                "render_id": render_id,
                "tier": tier,
                "subject": visual.subject[:120],
                "turn_number": sd.snapshot.turn_manager.interaction,
                "player_id": player_id,
                "room_slug": room_slug or "",
            },
            component="render",
        )

        # Pingpong 2026-04-26 [S3-PORT-REGRESSION] MAP_UPDATE — slice 1 of N
        # from the Rust port. When the narrator picks the cartography tier,
        # the daemon will render a map; without an accompanying MAP_UPDATE
        # the UI's MapOverlay/Automapper has no metadata to overlay on the
        # rendered image. Emit alongside the render dispatch (mirror of the
        # IMAGE/SCRAPBOOK twin pattern) so the map subsystem is visibly
        # engaged. Deferred to follow-up: location-change trigger,
        # journaling for replay. See pingpong file for the full deferred-
        # with-spec list.
        if tier == "cartography":
            try:
                self._emit_map_update_for_cartography(
                    sd=sd, render_id=render_id, player_id=player_id,
                )
            except Exception as exc:  # noqa: BLE001 — map emit must never crash a turn
                logger.warning(
                    "map_update.emit_failed render_id=%s error=%s",
                    render_id,
                    exc,
                )

        # Capture the legacy out_queue only as a fallback for the
        # pre-room-context test/legacy path. When `_room` is set (the
        # production slug-connect path) the completion handler looks the
        # queue up via the registry instead.
        legacy_queue = self._out_queue if room_slug is None else None
        asyncio.create_task(
            self._run_render(
                client,
                params,
                render_id,
                room_slug,
                player_id,
                legacy_queue,
            )
        )
        # ADR-050 — record the dispatch *after* the task is created so the
        # cooldown only starts ticking on actually-dispatched renders.
        # ``force_render`` callers intentionally skip this call to leave the
        # cadence untouched; this code path is the organic dispatch only.
        sd.image_pacing_throttle.record_render()

        return RenderQueuedMessage(
            type=MessageType.RENDER_QUEUED,  # type: ignore[arg-type]
            payload=RenderQueuedPayload(render_id=render_id),
            player_id=player_id,
        )

    # ------------------------------------------------------------------
    # Audio DJ dispatch — runs after NARRATION, ships AUDIO_CUE alongside.
    # Synchronous filesystem lookup; no daemon round-trip, no placeholder
    # message. See docs/superpowers/specs/2026-04-23-audio-dj-wiring-design.md
    # ------------------------------------------------------------------

    def _maybe_dispatch_audio(
        self,
        sd: _SessionData,
        result: object,
    ) -> AudioCueMessage | None:
        """Run the DJ: interpret narration → resolve tracks → return an
        AudioCueMessage, or None if any precondition fails. Best-effort;
        exceptions are caught and logged so audio never crashes a turn."""
        from sidequest.agents.orchestrator import NarrationTurnResult

        if not isinstance(result, NarrationTurnResult):
            return None
        if sd.audio_backend is None:
            self._audio_skip(sd, "no_audio_config")
            return None
        narration = (result.narration or "").strip()
        if not narration:
            self._audio_skip(sd, "no_narration")
            return None

        try:
            # Keep the span open across interpret + payload build so its
            # attributes can carry the *final* DJ decision (mood/track/
            # sfx). Playtest 2026-04-24 "sidequest.audio.dispatch span has
            # zero attributes — blind OTEL" — the prior impl opened the
            # span with no attributes and the GM panel couldn't tell why
            # the client was firing "Unable to decode audio data".
            with tracer.start_as_current_span("sidequest.audio.dispatch") as span:
                span.set_attribute("genre", sd.genre_slug)
                span.set_attribute("turn_number", sd.snapshot.turn_manager.interaction)
                cues = _AUDIO_INTERPRETER.interpret(
                    narration,
                    sd.audio_backend._config,  # type: ignore[attr-defined]
                )
                payload = build_audio_cue_payload(
                    cues,
                    audio_backend=sd.audio_backend,
                    genre_slug=sd.genre_slug,
                )
                # Emit the resolved cue shape so the GM panel can correlate
                # a turn's dispatch with the client-side decode errors.
                span.set_attribute("mood", payload.mood or "")
                span.set_attribute("music_track", payload.music_track or "")
                span.set_attribute("sfx_count", len(payload.sfx_triggers))
                if payload.sfx_triggers:
                    # Spans accept list attributes; truncate to keep the
                    # trace payload bounded even with long SFX batches.
                    span.set_attribute(
                        "sfx_triggers",
                        list(payload.sfx_triggers[:16]),
                    )
                span.set_attribute(
                    "reason",
                    "empty_cues"
                    if payload.mood is None and not payload.sfx_triggers
                    else "dispatched",
                )
        except Exception as exc:  # noqa: BLE001 — best-effort; never crash a turn
            logger.warning("audio.dispatch_failed error=%s", exc)
            self._audio_skip(sd, "error", extra={"error": type(exc).__name__})
            return None

        if payload.mood is None and not payload.sfx_triggers:
            self._audio_skip(sd, "empty_cues")
            return None

        self._audio_dispatched(sd, payload)
        return AudioCueMessage(
            payload=payload,
            player_id=sd.player_id,
        )

    def _audio_skip(
        self,
        sd: _SessionData,
        reason: str,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        # Span emission replaces the prior direct ``_watcher_publish`` —
        # ``WatcherSpanProcessor`` re-emits via
        # ``SPAN_ROUTES[SPAN_AUDIO_SKIPPED]``. ``extra`` is JSON-encoded
        # because OTEL drops dict attribute values; the route extract
        # returns the JSON string for dashboard parity.
        with audio_skipped_span(
            reason=reason,
            turn_number=sd.snapshot.turn_manager.interaction,
            extra=extra,
        ):
            pass

    def _audio_dispatched(
        self,
        sd: _SessionData,
        payload: AudioCuePayload,
    ) -> None:
        # Span emission replaces the prior direct ``_watcher_publish`` —
        # ``WatcherSpanProcessor`` re-emits via
        # ``SPAN_ROUTES[SPAN_AUDIO_DISPATCHED]``.
        with audio_dispatched_span(
            turn_number=sd.snapshot.turn_manager.interaction,
            mood=payload.mood or "",
            music_track=payload.music_track or "",
            sfx_count=len(payload.sfx_triggers),
        ):
            pass

    # ------------------------------------------------------------------
    # Lore embedding — RAG retrieval (pre-turn) + worker dispatch (post-turn)
    # ------------------------------------------------------------------

    async def _retrieve_lore_for_turn(self, sd: _SessionData, action: str) -> str | None:
        """Fetch the pre-turn lore block via semantic search.

        Always returns ``None`` on empty stores, missing daemons, or
        embed failures — the narrator will run without RAG injection,
        which is strictly better than crashing the turn. Expected failure
        modes (empty store, daemon unavailable, embed error, query too
        large) are logged inside :func:`retrieve_lore_context` and surface
        their own OTEL span attribute. The blanket ``except Exception``
        below exists precisely for paths those guards do not cover (e.g.
        a malformed daemon reply that raises ``KeyError`` from
        ``EmbedResponse`` construction) so a buggy codepath never crashes
        the turn.
        """
        try:
            return await retrieve_lore_context(sd.lore_store, action)
        except Exception as exc:  # noqa: BLE001 — RAG must never crash a turn
            logger.warning(
                "lore_retrieval.unexpected_exception action_len=%d error=%s",
                len(action),
                exc,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "lore_retrieval",
                    "op": "failed",
                    "reason": "unexpected_exception",
                    "error": type(exc).__name__,
                },
                component="lore",
                severity="error",
            )
            return None

    def _dispatch_embed_worker(self, sd: _SessionData) -> None:
        """Spawn a background embed worker for any newly-added lore.

        Fire-and-forget, but lifecycle-tracked. The worker itself checks
        :meth:`DaemonClient.is_available` before opening any connection
        and returns early with ``skipped_daemon_unavailable=True`` when
        the sidecar is absent — matching the render-dispatch graceful
        degradation pattern.

        Double-dispatch gate: if a previous worker for this session is
        still running, skip this turn's dispatch. The next turn will pick
        up the remaining pending fragments. This prevents two concurrent
        workers from racing at the ``await client.embed()`` yield point
        and double-incrementing the retry counter on the same fragment.
        """
        tracer = trace.get_tracer("sidequest.server.session_handler")
        previous = sd.embed_task
        if previous is not None and not previous.done():
            # Emit a span for the skip so the GM panel's OTEL audit trail
            # shows it alongside the worker's own ``lore_embedding.worker``
            # span. Watcher event stays as well for the live state_transition
            # stream.
            with tracer.start_as_current_span("lore_embedding.dispatch_skipped") as skip_span:
                skip_span.set_attribute("lore.skip_reason", "worker_still_running")
                skip_span.set_attribute("lore.turn_number", sd.snapshot.turn_manager.interaction)
            _watcher_publish(
                "state_transition",
                {
                    "field": "lore_embedding",
                    "op": "skipped",
                    "reason": "worker_still_running",
                    "turn_number": sd.snapshot.turn_manager.interaction,
                },
                component="lore",
            )
            return
        pending = sd.lore_store.pending_embedding_ids(max_retries=3)
        if not pending:
            return
        turn_number = sd.snapshot.turn_manager.interaction
        sd.embed_task = asyncio.create_task(self._run_embed_worker(sd, len(pending), turn_number))

    async def _run_embed_worker(
        self, sd: _SessionData, pending_count: int, turn_number: int
    ) -> None:
        """Background embed worker — never raises, always emits telemetry."""
        try:
            result = await embed_pending_fragments(sd.lore_store)
        except Exception as exc:  # noqa: BLE001 — worker cannot crash the loop
            logger.exception("lore_embedding.worker_exception")
            _watcher_publish(
                "state_transition",
                {
                    "field": "lore_embedding",
                    "op": "failed",
                    "reason": "exception",
                    "error": type(exc).__name__,
                    "turn_number": turn_number,
                },
                component="lore",
                severity="error",
            )
            return
        _watcher_publish(
            "state_transition",
            {
                "field": "lore_embedding",
                "op": "completed",
                "pending_at_dispatch": pending_count,
                "turn_number": turn_number,
                **result.as_dict(),
            },
            component="lore",
        )

    async def _run_render(
        self,
        client: DaemonClient,
        params: dict[str, object],
        render_id: str,
        room_slug: str | None,
        player_id: str,
        legacy_queue: asyncio.Queue[object] | None,
    ) -> None:
        """Background render coroutine — waits for the daemon reply, then
        enqueues an IMAGE message or logs a failure. Never raises; any
        exception is caught and surfaced as an OTEL watcher event.

        Routing (story 37-30): when ``room_slug`` is set, the IMAGE is
        delivered to the *current* outbound queue looked up via the
        RoomRegistry — so a reconnect mid-render still gets its image.
        ``legacy_queue`` is the pre-room-context fallback for
        constructions that haven't joined a room (used by older tests
        and the deprecated genre/world connect path)."""
        try:
            reply = await client.render(params)
        except DaemonUnavailableError as exc:
            logger.warning("render.reply_unavailable render_id=%s error=%s", render_id, exc)
            _watcher_publish(
                "state_transition",
                {
                    "field": "render",
                    "op": "failed",
                    "render_id": render_id,
                    "reason": "daemon_unavailable",
                    "error": str(exc),
                },
                component="render",
                severity="warning",
            )
            return
        except DaemonRequestError as exc:
            logger.warning(
                "render.reply_error render_id=%s code=%s error=%s",
                render_id,
                exc.code,
                exc.message,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "render",
                    "op": "failed",
                    "render_id": render_id,
                    "reason": "daemon_error",
                    "code": exc.code,
                    "error": exc.message,
                },
                component="render",
                severity="error",
            )
            return
        except Exception as exc:  # noqa: BLE001 — background task must never crash the loop
            logger.exception("render.reply_exception render_id=%s", render_id)
            _watcher_publish(
                "state_transition",
                {
                    "field": "render",
                    "op": "failed",
                    "render_id": render_id,
                    "reason": "exception",
                    "error": type(exc).__name__,
                },
                component="render",
                severity="error",
            )
            return

        image_url = str(reply.get("image_url") or "")
        # Self-healing render mount (S4-BUG): if the daemon restarted
        # mid-session its tmp dir changed; ensure_render_mount appends
        # the new dir to the live StaticFiles mount so /renders/* keeps
        # serving without a server restart. Falls back to the legacy
        # env-based rewriter so single-root paths (and unit tests that
        # don't wire app singleton) continue to work.
        from sidequest.server.render_mounts import (
            ensure_render_mount,
            get_active_app,
        )

        active_app = get_active_app()
        healed: str | None = (
            ensure_render_mount(active_app, image_url)
            if active_app is not None and image_url
            else None
        )
        served_url = healed if healed is not None else _render_url_from_path(image_url)
        width = int(reply.get("width") or 0) or None
        height = int(reply.get("height") or 0) or None
        elapsed = int(reply.get("elapsed_ms") or 0)

        msg = ImageMessage(
            type=MessageType.IMAGE,  # type: ignore[arg-type]
            payload=ImagePayload(
                url=served_url,
                render_id=render_id,
                tier=str(params.get("tier") or ""),
                width=width,
                height=height,
            ),
            player_id=player_id,
        )

        # Story 37-30 — resolve the live outbound queue at completion
        # time, not at dispatch. When the room is known, look up the
        # current socket for this player via the RoomRegistry; this is
        # the only path that survives a mid-render reconnect.
        target_queue: asyncio.Queue[object] | None
        if room_slug is not None:
            target_queue = None
            registry = self._room_registry
            if registry is not None:
                room = registry.get(room_slug)
                if room is not None:
                    socket_id = room.socket_for_player(player_id)
                    if socket_id is not None:
                        target_queue = room.queue_for_socket(socket_id)
            if target_queue is None:
                logger.warning(
                    "render.session_not_found render_id=%s room=%s player=%s",
                    render_id,
                    room_slug,
                    player_id,
                )
                _watcher_publish(
                    "state_transition",
                    {
                        "field": "render",
                        "op": "session_not_found",
                        "render_id": render_id,
                        "room_slug": room_slug,
                        "player_id": player_id,
                        "tier": str(params.get("tier") or ""),
                        "url": served_url,
                        "reason": "player_not_connected",
                    },
                    component="render",
                    severity="warning",
                )
                return
        else:
            target_queue = legacy_queue

        if target_queue is None:
            # Pre-room-context dispatch with no fallback queue — the
            # render had nowhere to land. Surface it loudly.
            logger.warning("render.session_not_found render_id=%s reason=no_queue", render_id)
            _watcher_publish(
                "state_transition",
                {
                    "field": "render",
                    "op": "session_not_found",
                    "render_id": render_id,
                    "player_id": player_id,
                    "tier": str(params.get("tier") or ""),
                    "url": served_url,
                    "reason": "no_outbound_queue",
                },
                component="render",
                severity="warning",
            )
            return

        try:
            target_queue.put_nowait(msg)
        except asyncio.QueueFull:
            logger.warning("render.outbound_queue_full render_id=%s", render_id)
            return
        logger.info(
            "render.completed render_id=%s url=%s elapsed_ms=%d",
            render_id,
            served_url,
            elapsed,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "render",
                "op": "completed",
                "render_id": render_id,
                "url": served_url,
                "elapsed_ms": elapsed,
                "player_id": player_id,
                "room_slug": room_slug or "",
            },
            component="render",
        )

    def _party_member_from_character(
        self,
        sd: _SessionData,
        character: Character,
        player_id: str,
        player_name: str,
    ) -> PartyMember:
        """Build a single PartyMember from a Character object.

        Factored out of :meth:`_build_session_start_party_status` so the
        same construction can run for the requesting socket's PC and for
        peer PCs that landed in the snapshot via multiplayer chargen.
        """
        # Inventory is stored as list[dict] in Phase 1 (creature_core.py:158).
        # Filter to Carried items — identical to Rust's inventory.carried()
        # iterator, which skips Stored/Dropped.
        carried = [
            item
            for item in character.core.inventory.items
            if str(item.get("state", "Carried")) == "Carried"
        ]

        stats = dict(character.stats)
        abilities = [a.name for a in character.abilities]
        equipment = [
            f"{item['name']} [equipped]" if item.get("equipped") else item["name"]
            for item in carried
        ]

        sheet = CharacterSheetDetails(
            race=NonBlankString(character.race),
            stats=stats,
            abilities=abilities,
            backstory=NonBlankString(character.backstory or "(no backstory)"),
            personality=NonBlankString(character.core.personality),
            pronouns=NonBlankString(character.pronouns) if character.pronouns else None,
            equipment=equipment,
        )

        # Currency noun from inventory.yaml::currency.name (pingpong
        # 2026-04-24 fantasy-leak bug). None → UI neutral fallback;
        # no silent default to "gold".
        currency_name: str | None = None
        if sd.genre_pack.inventory is not None and sd.genre_pack.inventory.currency is not None:
            currency_name = sd.genre_pack.inventory.currency.name

        inventory_payload = InventoryPayload(
            items=[
                InventoryItem(
                    name=NonBlankString(str(item["name"])),
                    # Protocol alias: "type". Dicts carry "category" from
                    # the loadout encoder; map and keep a non-blank string.
                    **{"type": str(item.get("category", "equipment") or "equipment")},  # type: ignore[arg-type]
                    equipped=bool(item.get("equipped", False)),
                    quantity=int(item.get("quantity", 1)),
                    description=NonBlankString(str(item.get("description") or item["name"])),
                )
                for item in carried
            ],
            gold=character.core.inventory.gold,
            currency_name=currency_name,
        )

        location_nbs: NonBlankString | None = None
        loc_display = _resolve_location_display(sd.genre_pack, sd.world_slug, sd.snapshot.location)
        if loc_display:
            try:
                location_nbs = NonBlankString(loc_display)
            except Exception:
                location_nbs = None

        class_nbs = NonBlankString(character.char_class or "Adventurer")
        char_name_nbs = NonBlankString(character.core.name)

        return PartyMember(
            player_id=NonBlankString(player_id or "anon"),
            name=NonBlankString(player_name or "Player"),
            character_name=char_name_nbs,
            current_hp=character.core.edge.current,
            max_hp=character.core.edge.max,
            statuses=[s.text for s in character.core.statuses],
            **{"class": class_nbs},  # type: ignore[arg-type]
            level=character.core.level,
            portrait_url=None,
            current_location=location_nbs,
            sheet=sheet,
            inventory=inventory_payload,
        )

    def _resolve_self_character(self, sd: _SessionData) -> Character | None:
        """Find the Character belonging to ``sd.player_id`` in the snapshot.

        Used to disambiguate "which PC is *me*" when the snapshot carries
        multiple PCs (multiplayer). Returning ``snapshot.characters[0]`` is
        wrong for any player whose seat isn't first — that's the playtest
        2026-04-25 "Tab 2 sees Laverne (YOU)" bug. The seat map (written at
        chargen-commit, lines 2440-2475) is the source of truth; the room
        seat is the live runtime mirror used as a fallback.

        Returns ``None`` for legacy saves with no ``player_seats`` binding
        AND no live room seat (very old solo saves). Callers should fall
        back to ``snapshot.characters[0]`` in that case to keep solo
        single-PC sessions working.
        """
        snapshot = sd.snapshot
        if not snapshot.characters:
            return None
        if sd.player_id and snapshot.player_seats:
            char_name = snapshot.player_seats.get(sd.player_id)
            if char_name:
                for c in snapshot.characters:
                    if c.core.name == char_name:
                        return c
        if sd.player_id and self._room is not None:
            seat_lookup = getattr(self._room, "slot_to_player_id", None)
            if callable(seat_lookup):
                for slot, pid in seat_lookup().items():
                    if pid == sd.player_id:
                        for c in snapshot.characters:
                            if c.core.name == slot:
                                return c
        return None

    def _build_session_start_party_status(
        self,
        sd: _SessionData,
        character: Character,
        player_id: str,
    ) -> PartyStatusMessage:
        """PARTY_STATUS frame at chargen end (Rust connect.rs:2533).

        MP: enumerates every PC; maps each slot back to its seating
        player_id via the room. Falls back to ``peer:<name>`` when
        no seat record is available.
        """
        seat_map: dict[str, str] = {}
        if self._room is not None:
            seat_lookup = getattr(self._room, "slot_to_player_id", None)
            if callable(seat_lookup):
                seat_map = seat_lookup()

        members: list[PartyMember] = []
        all_chars = list(sd.snapshot.characters or [])
        if not all_chars:
            all_chars = [character]
        # Stable ordering: self first, then peers in snapshot order.
        self_chars = [c for c in all_chars if c.core.name == character.core.name]
        peer_chars = [c for c in all_chars if c.core.name != character.core.name]
        for char in self_chars + peer_chars:
            is_self = char.core.name == character.core.name
            if is_self:
                pid = player_id or "anon"
                pname = sd.player_name or "Player"
            else:
                pid = seat_map.get(char.core.name) or f"peer:{char.core.name}"
                pname = char.core.name
            members.append(self._party_member_from_character(sd, char, pid, pname))

        return PartyStatusMessage(
            type="PARTY_STATUS",  # type: ignore[arg-type]
            payload=PartyStatusPayload(members=members),
            player_id=player_id,
        )


# ---------------------------------------------------------------------------
# Module-level helpers — extracted to session_helpers.py and narration_apply.py.
# Re-exported here so existing imports (tests, external callers) keep working.
# ---------------------------------------------------------------------------

from sidequest.server.narration_apply import (  # noqa: E402 — back-compat re-export
    _apply_narration_result_to_snapshot,
)
from sidequest.server.session_helpers import (  # noqa: E402 — back-compat re-export
    _build_turn_context,
    _detect_npc_identity_drift,
    _error_msg,
    _find_confrontation_def,
    _presence_msg,
    _render_url_from_path,
    _resolve_acting_character_name,
    _resolve_location_display,
    _sfx_ids_from_genre,
    _world_history_value,
    aggregate_visibility,
    build_secret_note_events,
    emit_secret_notes,
)

__all__ = [
    # Top-level types defined in this module
    "SentFrame",
    "WebSocketSessionHandler",
    "_SessionData",
    "_State",
    # Module-level helpers re-exported from session_helpers / narration_apply
    "_apply_narration_result_to_snapshot",
    "_build_turn_context",
    "_detect_npc_identity_drift",
    "_error_msg",
    "_find_confrontation_def",
    "_presence_msg",
    "_render_url_from_path",
    "_resolve_acting_character_name",
    "_resolve_location_display",
    "_sfx_ids_from_genre",
    "_world_history_value",
    "aggregate_visibility",
    "apply_turn_writes_for_test",
    "build_secret_note_events",
    "emit_secret_notes",
]
