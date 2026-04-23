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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from sidequest.server.session_room import RoomRegistry, SessionRoom

from sidequest.agents.claude_client import ClaudeClient, ClaudeLike
from sidequest.agents.orchestrator import NpcMention, Orchestrator, TurnContext
from sidequest.game.archetype_apply import apply_archetype_resolved
from sidequest.game.builder import (
    BuilderError,
    CharacterBuilder,
)
from sidequest.game.character import Character
from sidequest.game.lore_seeding import seed_lore_from_char_creation
from sidequest.game.lore_store import LoreStore
from sidequest.game.persistence import SqliteStore, db_path_for_session
from sidequest.game.region_init import RegionInitError, init_region_location
from sidequest.game.room_movement import (
    RoomGraphInitError,
    init_room_graph_location,
)
from sidequest.game.session import GameSnapshot, NarrativeEntry, NpcRegistryEntry
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
from sidequest.game.event_log import EventLog
from sidequest.game.projection_filter import PassThroughFilter
from sidequest.protocol import GameMessage, sanitize_player_text
from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    ErrorPayload,
    GamePausedMessage,
    GamePausedPayload,
    GameResumedMessage,
    NarrationEndMessage,
    NarrationEndPayload,
    NarrationMessage,
    NarrationPayload,
    PartyStatusMessage,
    PartyStatusPayload,
    PlayerPresenceMessage,
    PlayerPresencePayload,
    SeatConfirmedMessage,
    SeatConfirmedPayload,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.protocol.models import (
    CharacterSheetDetails,
    Footnote,
    InventoryItem,
    InventoryPayload,
    PartyMember,
)
from sidequest.protocol.types import NonBlankString
from sidequest.server.dispatch.chargen_loadout import apply_starting_loadout
from sidequest.server.dispatch.chargen_summary import render_confirmation_summary
from sidequest.server.dispatch.culture_context import resolve_culture_reference
from sidequest.server.dispatch.opening_hook import resolve_opening
from sidequest.server.dispatch.scenario_bind import bind_scenario
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish
from sidequest.telemetry.spans import (
    SPAN_NPC_AUTO_REGISTERED,
    SPAN_NPC_REINVENTED,
    SPAN_ORCHESTRATOR_PROCESS_ACTION,  # noqa: F401 — re-exported for OTEL catalog consumers
    orchestrator_process_action_span,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event-kind → message class mapping (MP-03 Task 3)
# Extend this dict as additional kinds are routed through _emit_event.
# ---------------------------------------------------------------------------

_KIND_TO_MESSAGE_CLS: dict[str, type] = {
    "NARRATION": NarrationMessage,
}


# ---------------------------------------------------------------------------
# Replay helper (MP-03 Task 4)
# Reconstructs a typed protocol message from a persisted EventRow on reconnect.
# Distinct from _emit_event (live fan-out) but reuses _KIND_TO_MESSAGE_CLS as
# the single source of truth for kind → message class mapping.
# ---------------------------------------------------------------------------

def _build_message_for_kind(*, kind: str, payload_json: str, seq: int) -> object:
    """Build a typed protocol message from a persisted event row for replay.

    Raises ValueError on unknown kinds — no silent fallback.
    Caller: slug-connect branch, after SESSION_CONNECTED is built, to
    reconstruct missed events since last_seen_seq.
    """
    import json

    message_cls = _KIND_TO_MESSAGE_CLS.get(kind)
    if message_cls is None:
        raise ValueError(f"_build_message_for_kind: unknown event kind {kind!r}")

    data = json.loads(payload_json)
    data["seq"] = seq

    if kind == "NARRATION":
        from sidequest.protocol.messages import NarrationPayload as _NarrationPayload
        return message_cls(payload=_NarrationPayload(**data))

    # Unreachable: _KIND_TO_MESSAGE_CLS guard above catches unknowns.
    # Kept as a belt-and-suspenders hard fail.
    raise ValueError(f"_build_message_for_kind: no payload constructor for kind {kind!r}")


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
    mode: "GameMode | None" = None


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
        claude_client_factory: Callable[[], ClaudeLike] | None = None,
        genre_pack_search_paths: list[Path] | None = None,
        save_dir: Path,
    ) -> None:
        self._client_factory: Callable[[], ClaudeLike] = (
            claude_client_factory if claude_client_factory is not None
            else ClaudeClient
        )
        self._search_paths: list[Path] = (
            genre_pack_search_paths
            if genre_pack_search_paths is not None
            else DEFAULT_GENRE_PACK_SEARCH_PATHS
        )
        self._save_dir = save_dir
        self._state = _State.AwaitingConnect
        self._session_data: _SessionData | None = None
        # Room context fields — populated by attach_room_context() during the
        # WebSocket lifecycle (ws_endpoint). Absent here means the handler is
        # being driven outside that lifecycle (e.g. unit tests that exercise
        # non-slug-connect code paths). The slug-connect branch rejects this
        # loudly rather than silently skipping room wiring.
        self._room_registry: "RoomRegistry | None" = None
        self._socket_id: str | None = None
        self._out_queue: "asyncio.Queue[object] | None" = None
        self._room: "SessionRoom | None" = None
        # EventLog + projection filter are bound in the slug-connect branch.
        # The legacy genre/world connect path leaves them None; _emit_event
        # falls back to a plain message without seq in that case. This is a
        # real production code path (not a test-only skip), documented below.
        self._event_log: EventLog | None = None
        self._projection_filter: PassThroughFilter | None = None

    # ------------------------------------------------------------------
    # Room context (MP-02 Task 2)
    # ------------------------------------------------------------------

    def attach_room_context(
        self,
        *,
        registry: "RoomRegistry",
        socket_id: str,
        out_queue: "asyncio.Queue[object]",
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

    def current_room(self) -> "SessionRoom | None":
        """Return the room this handler is currently registered in, or None."""
        return self._room

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
            # Invariant 1: persist first
            row = event_log.append(kind=kind, payload_json=payload_json)
            seq = row.seq

            # Build emitter's message with raw, unfiltered payload + seq (Invariant 3)
            if isinstance(payload_model, BaseModel):
                emitter_payload = payload_model.model_copy(update={"seq": seq})
            else:
                emitter_payload = payload_model  # type: ignore[assignment]
            out_to_self = message_cls(payload=emitter_payload)

            # Fan-out to other connected players (Invariant 2)
            room = self._room
            if room is not None and projection_filter is not None:
                emitter_player_id = (
                    self._session_data.player_id if self._session_data else None
                )
                for other_pid in room.connected_player_ids():
                    if other_pid == emitter_player_id:
                        continue
                    decision = projection_filter.project(event=row, player_id=other_pid)
                    if not decision.include:
                        continue
                    # Build per-recipient payload: merge filtered payload_json + seq
                    try:
                        filtered_data = json.loads(decision.payload_json)
                        filtered_data["seq"] = seq
                        if isinstance(payload_model, BaseModel):
                            recipient_payload = payload_model.model_copy(
                                update={**json.loads(decision.payload_json), "seq": seq}
                            )
                        else:
                            recipient_payload = filtered_data  # type: ignore[assignment]
                        recipient_msg = message_cls(payload=recipient_payload)
                    except Exception:
                        # Never silently fail fan-out; log and skip this recipient
                        logger.error(
                            "emit_event.fanout_failed kind=%s other_pid=%s",
                            kind,
                            other_pid,
                        )
                        continue
                    socket_id = room.socket_for_player(other_pid)
                    if socket_id is None:
                        continue
                    queue = room.queue_for_socket(socket_id)
                    if queue is not None:
                        queue.put_nowait(recipient_msg)
        else:
            # Legacy path (non-slug connect): no EventLog, no seq
            out_to_self = message_cls(payload=payload_model)

        return out_to_self

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
            try:
                self._session_data.store.save(self._session_data.snapshot)
                logger.info(
                    "session.disconnect_save genre=%s world=%s player=%s",
                    self._session_data.genre_slug,
                    self._session_data.world_slug,
                    self._session_data.player_name,
                )
            except Exception as exc:
                logger.error("session.disconnect_save_failed error=%s", exc)
            finally:
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
            row = get_game(store, slug)
            if row is None:
                return [_error_msg(f"unknown game slug: {slug}")]
            if not player_id:
                player_id = str(uuid.uuid4())

            # Room registry wiring (MP-02 Task 2). attach_room_context must
            # have been called — slug-connect cannot proceed without a room
            # registry, socket id, and outbound queue. Fail loudly if the
            # WebSocket lifecycle was bypassed (no silent test-only path).
            if (
                self._room_registry is None
                or self._socket_id is None
                or self._out_queue is None
            ):
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
                    row.genre_slug, slug, exc,
                )
                return [_error_msg(f"Failed to load genre pack '{row.genre_slug}': {exc}")]

            # Restore saved snapshot, or start fresh (Bug 2 fix: resume semantics).
            saved = store.load()
            if saved is not None:
                snapshot = saved.snapshot
                has_character = bool(snapshot.characters)
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
                has_character = False
                logger.info(
                    "session.slug_new_session genre=%s world=%s slug=%s",
                    row.genre_slug,
                    row.world_slug,
                    slug,
                )

            self._session_data = _SessionData(
                genre_slug=row.genre_slug,
                world_slug=row.world_slug,
                player_name=player_id,
                player_id=player_id,
                snapshot=snapshot,
                store=store,
                genre_pack=genre_pack,
                orchestrator=Orchestrator(client=self._client_factory()),
                game_slug=slug,
                mode=GameMode(row.mode),
            )
            # MP-03 Task 3: initialize EventLog + ProjectionFilter for sync layer.
            self._event_log = EventLog(store)
            self._projection_filter = PassThroughFilter()
            self._last_seen_seq = payload.last_seen_seq or 0
            self._current_player_id = player_id
            self._state = _State.Creating if not has_character else _State.Playing
            connected_msg = SessionEventMessage(
                type="SESSION_EVENT",  # type: ignore[arg-type]
                payload=SessionEventPayload(
                    event="connected",
                    player_name=player_id,
                    genre=row.genre_slug,
                    world=row.world_slug,
                    has_character=has_character,
                ),
                player_id=player_id,
            )

            # MP-03 Task 4: replay missed events since last_seen_seq.
            # SESSION_CONNECTED is always first; replay follows in seq ASC order.
            missed = self._event_log.read_since(since_seq=self._last_seen_seq)
            replay_msgs: list[object] = []
            for event_row in missed:
                dec = self._projection_filter.project(
                    event=event_row, player_id=self._current_player_id
                )
                if not dec.include:
                    continue
                replay_msgs.append(
                    _build_message_for_kind(
                        kind=event_row.kind,
                        payload_json=dec.payload_json,
                        seq=event_row.seq,
                    )
                )
            return [connected_msg, *replay_msgs]

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

        # Load existing session or start fresh
        saved = store.load()
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
        opening: tuple[str, str] | None = resolve_opening(
            genre_pack, world_slug, genre_slug
        )
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

        self._session_data = _SessionData(
            genre_slug=genre_slug,
            world_slug=world_slug,
            player_name=player_name,
            player_id=player_id,
            snapshot=snapshot,
            store=store,
            genre_pack=genre_pack,
            orchestrator=orchestrator,
            builder=builder,
            opening_seed=opening_seed,
            opening_directive=opening_directive,
            world_context=world_context,
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
            # turn fires.
            if snapshot.characters:
                try:
                    outbound.append(
                        self._build_session_start_party_status(
                            self._session_data, snapshot.characters[0], player_id
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "session.resume_party_status_failed error=%s", exc
                    )

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

        span.add_event(
            "character_creation.character_built",
            {
                "event": "character_built",
                "name": character.core.name,
                "class": character.char_class,
                "race": character.race,
                "hp": character.core.edge.current,
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

        # World materialization (Story 2.3 Slice C). Build a fresh
        # snapshot from pack history at Fresh maturity (chargen is always
        # Fresh — the player just arrived), then inject the built
        # character. Replaces ``sd.snapshot`` so history chapters' lore,
        # NPCs, notes, and scene context (location/time_of_day/atmosphere/
        # active_stakes) populate the snapshot the narrator will read.
        # Rust parity: connect.rs:1892-1946.
        #
        # Parse failure → log-and-fall-back to an empty snapshot with
        # just genre/world slugs set (Rust parity: ``unwrap_or_else``
        # with a warn log). The dispatch must not hard-fail on malformed
        # pack history — the player is mid-confirm and the character is
        # already built.
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
            materialized = GameSnapshot(
                genre_slug=sd.genre_slug, world_slug=sd.world_slug
            )
        # The fresh chapter may have authored an "Adventurer" placeholder
        # character — discard it; the chargen-built character owns that
        # slot. Inventory on the built character already reflects the
        # post-loadout state from Slice A.
        materialized.characters = [character]
        sd.snapshot = materialized
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

        # Scenario binding (Story 2.3 Slice D). When the pack declares a
        # scenario, bind the first one to a ScenarioState, seed matching
        # NPC belief states, and stash the chosen pack on the session
        # for later consumers (pressure events, scene budget, accusation
        # UI). Rust parity: connect.rs:1948-2023. No-op when the pack
        # has no scenarios.
        bind_result = bind_scenario(
            sd.genre_pack,
            sd.snapshot,
            genre_slug=sd.genre_slug,
            world_slug=sd.world_slug,
        )
        if bind_result is not None:
            _, active_pack = bind_result
            sd.active_scenario = active_pack

        # Room-graph init (Story 2.3 Slice E). When the selected world
        # uses ``navigation_mode: room_graph`` and loaded a non-empty
        # rooms list, set ``snap.location`` to the entrance room. Rust
        # parity: connect.rs:2025-2069. No-op for region-mode worlds
        # (the rules-based default_location path handles those; lands
        # with a later slice when the snapshot currently leaves it
        # blank). RoomGraphInitError is a pack authoring bug — log
        # loudly at error level and leave ``snap.location`` blank
        # rather than hard-fail the confirmation frame.
        world = sd.genre_pack.worlds.get(sd.world_slug)

        # Region init (Story 37-31). Runs for every world that carries
        # cartography, regardless of navigation mode: region-mode worlds
        # need current_region to be their canonical location, and
        # room_graph worlds still surface a region label alongside the
        # room-level position so the Map tab is load-bearing from turn
        # 1. RegionInitError is a pack authoring bug (missing / stale
        # starting_region) — log loudly at error level and leave
        # current_region blank rather than strand the player mid-
        # commit, matching the room_graph error path.
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

        # Lore seeding (Story 2.3 Slice F). Must run BEFORE clearing
        # the builder — the builder owns the scene list, and the
        # seeder reads per-choice label/description text to build
        # Character-category lore fragments. Rust parity:
        # connect.rs:2196-2201 ("seed_lore_from_char_creation BEFORE
        # clearing the builder"). Without this, backstory choices
        # made during chargen are invisible to the narrator's RAG
        # retrieval pipeline.
        lore_added = seed_lore_from_char_creation(
            sd.lore_store, list(builder.scenes())
        )
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

        # NPC registry reset (Story 2.3 Slice G). A fresh character
        # entering the world must not inherit chargen-tier NPC name
        # extractions (the character's own name, lobby filler, etc.).
        # World-level state — lore, tropes, region discovery, world
        # history — MUST persist; only the per-narration NPC registry
        # is wiped. Rust parity: connect.rs:2136-2157.
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

        # Persist (Story 2.3 Slice G). Writes the fully-populated
        # snapshot — character in characters[], NPCs from world
        # materialization, scenario bound, room-graph entrance set
        # — to SQLite so a reconnect hits has_character=True and
        # skips chargen. Without this, the player walks chargen
        # again on every connect. Rust parity: connect.rs:2174-2180.
        try:
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
            # Persistence failure must NOT strand the player mid-
            # commit — log loudly (not silent) and proceed. On next
            # reconnect the save will be absent so chargen repeats;
            # the OTEL event surfaces the underlying fault for the
            # GM panel / ops review.
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

        # Flip state to Playing (Story 2.3 Slice G). Before this, the
        # session stayed in Creating until the first PLAYER_ACTION —
        # which meant a disconnect-between-confirmation-and-first-
        # action lost the save state flag. Now the transition happens
        # atomically with persistence. Rust parity: connect.rs:2183
        # (session.complete_character_creation).
        self._state = _State.Playing

        sd.builder = None
        logger.info(
            "chargen.complete char_name=%s class=%s race=%s hp=%d",
            character.core.name,
            character.char_class,
            character.race,
            character.core.edge.current,
        )

        payload = CharacterCreationPayload(
            phase="complete",
            total_scenes=builder.total_scenes(),
            character=character.model_dump(mode="json"),
        )
        out: list[object] = [
            CharacterCreationMessage(payload=payload, player_id=player_id)
        ]

        # PARTY_STATUS snapshot (Story 2.3 Slice H / Rust connect.rs:2533-2609).
        # Emits the populated character sheet so the client Character
        # tab lands populated at session-start without waiting for the
        # first turn's PARTY_STATUS update.
        try:
            out.append(
                self._build_session_start_party_status(sd, character, player_id)
            )
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
                            i for i in character.core.inventory.items
                            if str(i.get("state", "Carried")) == "Carried"
                        ]
                    ),
                },
            )
        except Exception as exc:
            # The character-snapshot frame is a convenience for the UI;
            # a failure to build it MUST NOT block the player from
            # entering the world. Log loud and continue.
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

        # Opening-turn bootstrap (Story 2.3 Slice H / Rust connect.rs:2270-2529).
        # Fires the narrator using ``opening_seed`` (or a generic look-
        # around fallback) and ``opening_directive`` injected into the
        # Early zone. Consumes both fields on the session so subsequent
        # PLAYER_ACTION turns run directive-free.
        opening_messages = await self._run_opening_turn_narration(
            sd, player_id, span
        )
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
            return [
                render_confirmation_summary(
                    builder, sd.genre_pack, sd.player_name, player_id
                )
            ]
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
            player_id_attr = (
                self._session_data.player_id if self._session_data else ""
            )
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
        turn_context = _build_turn_context(sd)
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

        with orchestrator_process_action_span(action_len=len(action)):
            result = await sd.orchestrator.run_narration_turn(action, turn_context)

        logger.info(
            "session.narration_complete genre=%s world=%s degraded=%s duration_ms=%s",
            sd.genre_slug,
            sd.world_slug,
            result.is_degraded,
            result.agent_duration_ms,
        )

        _apply_narration_result_to_snapshot(snapshot, result, sd.player_name)
        snapshot.turn_manager.record_interaction()

        try:
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
                "session.persisted turn=%d player=%s",
                snapshot.turn_manager.interaction,
                sd.player_name,
            )
        except Exception as exc:
            logger.error("session.persist_failed error=%s", exc)

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
        )
        # MP-03 Task 3: route through EventLog + ProjectionFilter before send.
        narration_msg = self._emit_event("NARRATION", narration_payload)

        outbound: list[object] = [
            narration_msg,
            NarrationEndMessage(
                type="NARRATION_END",  # type: ignore[arg-type]
                payload=NarrationEndPayload(state_delta=None),
                player_id=sd.player_id,
            ),
        ]

        # Refresh PARTY_STATUS so `current_location` and any HP/inventory
        # mutations landed by the narration apply propagate to the client
        # header / CharacterSheet / MapOverlay. Previously PARTY_STATUS
        # was emitted exactly once at chargen-end (before the opening
        # turn), which froze the location at its pre-opening value
        # (typically empty). Playtest 2026-04-22.
        if snapshot.characters:
            try:
                party_status = self._build_session_start_party_status(
                    sd, snapshot.characters[0], sd.player_id
                )
                outbound.append(party_status)
                logger.info(
                    "state.party_status_emitted reason=turn_end location=%r turn=%d",
                    snapshot.location or "",
                    snapshot.turn_manager.interaction,
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
                logger.warning(
                    "state.party_status_refresh_failed error=%s", exc
                )

        # Semantic watcher event — `turn_complete` is the highest-leverage
        # frame the dashboard consumes. It unlocks Timeline rows, Subsystems
        # turn-buckets, Timing p95, and the Turn counter all at once.
        # Field shape mirrors `TurnCompleteFields` in
        # `sidequest-ui/src/types/watcher.ts`.
        try:
            patches: list[dict[str, object]] = []
            if result.location:
                patches.append({"patch_type": "location", "fields_changed": ["location"]})
            if result.quest_updates:
                patches.append(
                    {"patch_type": "quest", "fields_changed": list(result.quest_updates)}
                )
            if result.lore_established:
                patches.append({"patch_type": "lore", "fields_changed": ["lore_established"]})
            if result.npcs_present:
                patches.append(
                    {
                        "patch_type": "npc_registry",
                        "fields_changed": [n.name for n in result.npcs_present],
                    }
                )
            if result.items_gained or result.items_lost:
                patches.append({"patch_type": "inventory", "fields_changed": []})

            beats_fired: list[dict[str, object]] = []
            for beat in result.beat_selections or []:
                beats_fired.append(
                    {
                        "trope": getattr(beat, "trope_id", None)
                        or getattr(beat, "beat_id", None)
                        or "unknown",
                        "threshold": getattr(beat, "threshold", None),
                    }
                )

            _watcher_publish(
                "turn_complete",
                {
                    "turn_number": snapshot.turn_manager.interaction,
                    "classified_intent": result.classified_intent,
                    "agent_name": result.agent_name,
                    "agent_duration_ms": result.agent_duration_ms,
                    "total_duration_ms": result.agent_duration_ms,
                    "is_degraded": result.is_degraded,
                    "token_count_in": result.token_count_in,
                    "token_count_out": result.token_count_out,
                    "extraction_tier": str(result.prompt_tier),
                    "player_input": action,
                    "player_id": sd.player_id,
                    "genre": sd.genre_slug,
                    "world": sd.world_slug,
                    "patches": patches,
                    "beats_fired": beats_fired,
                    "delta_empty": not patches and not beats_fired,
                },
                component="orchestrator",
                severity="warning" if result.is_degraded else "info",
            )
        except Exception as exc:  # noqa: BLE001 — dashboard is best-effort; never crash a turn
            logger.warning("watcher.turn_complete_publish_failed error=%s", exc)

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
        action = sd.opening_seed or "I look around and take in my surroundings."
        source_tier = "world_or_genre_hook" if sd.opening_seed else "fallback"

        turn_context = _build_turn_context(
            sd, opening_directive=sd.opening_directive
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
            },
        )

        messages = await self._execute_narration_turn(sd, action, turn_context)

        # Consume once — Rust uses `opening_directive.take()`; subsequent
        # turns must run directive-free. Same for the seed: it's a
        # one-shot bootstrap action, not a recurring input.
        sd.opening_seed = None
        sd.opening_directive = None

        return messages

    def _build_session_start_party_status(
        self,
        sd: _SessionData,
        character: Character,
        player_id: str,
    ) -> PartyStatusMessage:
        """Build the PARTY_STATUS frame emitted at the end of chargen.

        Carries a fully populated :class:`CharacterSheetDetails` so the
        client's Character tab lands populated on session-start. Rust
        parity: connect.rs:2533-2609 (`session_start_party_status`).
        """
        # Inventory is stored as list[dict] in Phase 1 (creature_core.py:158).
        # Filter to Carried items — identical to Rust's inventory.carried()
        # iterator, which skips Stored/Dropped.
        carried = [
            item for item in character.core.inventory.items
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

        inventory_payload = InventoryPayload(
            items=[
                InventoryItem(
                    name=NonBlankString(str(item["name"])),
                    # Protocol alias: "type". Dicts carry "category" from
                    # the loadout encoder; map and keep a non-blank string.
                    **{"type": str(item.get("category", "equipment") or "equipment")},  # type: ignore[arg-type]
                    equipped=bool(item.get("equipped", False)),
                    quantity=int(item.get("quantity", 1)),
                    description=NonBlankString(
                        str(item.get("description") or item["name"])
                    ),
                )
                for item in carried
            ],
            gold=character.core.inventory.gold,
        )

        location_nbs: NonBlankString | None = None
        loc_value = sd.snapshot.location or ""
        if loc_value:
            try:
                location_nbs = NonBlankString(loc_value)
            except Exception:
                location_nbs = None

        class_nbs = NonBlankString(character.char_class or "Adventurer")
        char_name_nbs = NonBlankString(character.core.name)

        member = PartyMember(
            player_id=NonBlankString(player_id or "anon"),
            name=NonBlankString(sd.player_name or "Player"),
            character_name=char_name_nbs,
            current_hp=character.core.edge.current,
            max_hp=character.core.edge.max,
            statuses=list(character.core.statuses),
            **{"class": class_nbs},  # type: ignore[arg-type]
            level=character.core.level,
            portrait_url=None,
            current_location=location_nbs,
            sheet=sheet,
            inventory=inventory_payload,
        )

        return PartyStatusMessage(
            type="PARTY_STATUS",  # type: ignore[arg-type]
            payload=PartyStatusPayload(members=[member]),
            player_id=player_id,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_turn_context(
    sd: _SessionData, *, opening_directive: str | None = None
) -> TurnContext:
    """Assemble the :class:`TurnContext` for a single narration turn.

    Shared by :meth:`_handle_player_action` and the opening-turn
    bootstrap (Slice H). ``opening_directive`` is consumed by the
    narrator on turn 0 only — the caller is responsible for clearing
    the session-level directive after the turn runs.
    """
    snapshot = sd.snapshot
    char_name = (
        snapshot.characters[0].core.name if snapshot.characters else sd.player_name
    )
    return TurnContext(
        in_combat=False,
        in_chase=False,
        in_encounter=False,
        state_summary=snapshot.model_dump_json(indent=2),
        narrator_verbosity="standard",
        narrator_vocabulary="literary",
        genre=sd.genre_slug,
        genre_prompts=sd.genre_pack.prompts,
        character_name=char_name,
        current_location=snapshot.location or "Unknown",
        available_sfx=_sfx_ids_from_genre(sd.genre_pack),
        npc_registry=list(snapshot.npc_registry),
        npcs=list(snapshot.npcs),
        opening_directive=opening_directive,
        world_context=sd.world_context,
    )


def _world_history_value(pack: GenrePack, world_slug: str) -> object | None:
    """Extract the raw world ``history.yaml`` payload for a world.

    Rust reads ``pack.worlds.get(world).and_then(|w| w.history.as_ref())``;
    the Python loader stores ``history`` on ``World`` as an untyped
    ``Any`` (loader.py:383). Returns ``None`` when the world doesn't
    declare history — ``materialize_from_genre_pack`` treats ``None`` as
    zero chapters, producing a snapshot with just genre/world slugs set.
    """
    world = pack.worlds.get(world_slug)
    if world is None:
        return None
    return world.history


def _error_msg(message: str, reconnect_required: bool = False) -> ErrorMessage:
    return ErrorMessage(
        type="ERROR",  # type: ignore[arg-type]
        payload=ErrorPayload(
            message=NonBlankString(message),
            reconnect_required=reconnect_required,
        ),
        player_id="",
    )


def _presence_msg(player_id: str, state: str) -> PlayerPresenceMessage:
    """Build a PLAYER_PRESENCE message for connect/disconnect events (MP-02 Task 4)."""
    return PlayerPresenceMessage(
        payload=PlayerPresencePayload(player_id=player_id, state=state),  # type: ignore[arg-type]
    )


def _sfx_ids_from_genre(genre_pack: GenrePack) -> list[str]:
    """Extract SFX IDs from genre audio config."""
    if genre_pack.audio is None:
        return []
    sfx_lib = getattr(genre_pack.audio, "sfx_library", None)
    if not sfx_lib:
        return []
    if isinstance(sfx_lib, list):
        return [str(getattr(s, "id", s)) for s in sfx_lib]
    return []


def _apply_narration_result_to_snapshot(
    snapshot: GameSnapshot,
    result: object,
    player_name: str,
) -> None:
    """Apply game_patch extracted fields from NarrationTurnResult to the snapshot.

    Phase 1: location, quest_updates, lore_established, npc_registry updates.
    Phase 2+: items, HP changes, encounter state — deferred.
    """
    from sidequest.agents.orchestrator import NarrationTurnResult

    if not isinstance(result, NarrationTurnResult):
        return

    # Location update
    if result.location:
        old_loc = snapshot.location
        snapshot.location = result.location
        if result.location not in snapshot.discovered_regions:
            snapshot.discovered_regions.append(result.location)
        logger.info(
            "state.location_update old=%r new=%r player=%s",
            old_loc,
            result.location,
            player_name,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "location",
                "before": old_loc,
                "after": result.location,
                "player_name": player_name,
                "turn_number": snapshot.turn_manager.interaction,
                "discovered_count": len(snapshot.discovered_regions),
            },
            component="state.location",
        )

    # Quest updates
    if result.quest_updates:
        for quest_id, status in result.quest_updates.items():
            snapshot.quest_log[quest_id] = status
        logger.info(
            "state.quest_update count=%d player=%s",
            len(result.quest_updates),
            player_name,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "quest_log",
                "updates": dict(result.quest_updates),
                "player_name": player_name,
                "turn_number": snapshot.turn_manager.interaction,
            },
            component="quest_log",
        )

    # Lore established
    if result.lore_established:
        for lore in result.lore_established:
            if lore not in snapshot.lore_established:
                snapshot.lore_established.append(lore)

    # NPC registry — upsert from npcs_present (story 37-44: auto-register +
    # drift detection). Fires `npc.auto_registered` for new entries and
    # `npc.reinvented` when narrator-provided pronouns/role diverge from the
    # canonical registry entry, so the GM panel can surface identity drift.
    turn_num = snapshot.turn_manager.interaction
    for npc_mention in result.npcs_present:
        existing = next(
            (e for e in snapshot.npc_registry if e.name.lower() == npc_mention.name.lower()),
            None,
        )
        if existing is None:
            snapshot.npc_registry.append(
                NpcRegistryEntry(
                    name=npc_mention.name,
                    role=npc_mention.role or None,
                    pronouns=npc_mention.pronouns or None,
                    appearance=npc_mention.appearance or None,
                    last_seen_location=snapshot.location or None,
                    last_seen_turn=turn_num,
                )
            )
            logger.info(
                "%s name=%r pronouns=%r role=%r turn=%d",
                SPAN_NPC_AUTO_REGISTERED,
                npc_mention.name,
                npc_mention.pronouns or "",
                npc_mention.role or "",
                turn_num,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "npc_registry",
                    "op": "auto_registered",
                    "name": npc_mention.name,
                    "pronouns": npc_mention.pronouns or "",
                    "role": npc_mention.role or "",
                    "turn_number": turn_num,
                    "registry_len": len(snapshot.npc_registry),
                },
                component="npc_registry",
            )
        else:
            _detect_npc_identity_drift(existing, npc_mention, turn_num)
            existing.last_seen_turn = turn_num
            existing.last_seen_location = snapshot.location or None
            # Additive-only upsert: never overwrite a canonical field once set.
            # Without this guard, _detect_npc_identity_drift logs drift and then
            # the drifted value silently canonicalizes — warning fires once,
            # then the registry permanently holds the wrong identity.
            if npc_mention.role and not existing.role:
                existing.role = npc_mention.role
            if npc_mention.pronouns and not existing.pronouns:
                existing.pronouns = npc_mention.pronouns
            if npc_mention.appearance and not existing.appearance:
                existing.appearance = npc_mention.appearance


def _detect_npc_identity_drift(
    existing: NpcRegistryEntry,
    mention: NpcMention,
    turn_num: int,
) -> None:
    """Warn when a narrator-provided NPC mention disagrees with the canonical
    registry entry on pronouns or role. Fires `npc.reinvented` at WARNING
    level so the GM panel can surface drift (story 37-44).

    Empty fields on the mention are treated as "no opinion" and never trigger
    drift — only explicit disagreement counts. Pronoun and role comparisons
    are case-insensitive. No return value; side-effect only (logger.warning).
    Does not mutate `existing` or `mention`.
    """
    for field, m_val, e_val in (
        ("pronouns", mention.pronouns, existing.pronouns),
        ("role", mention.role, existing.role),
    ):
        if m_val and e_val and m_val.strip().lower() != e_val.strip().lower():
            logger.warning(
                "%s name=%r field=%s expected=%r narrator=%r turn=%d",
                SPAN_NPC_REINVENTED,
                existing.name,
                field,
                e_val,
                m_val,
                turn_num,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "npc_registry",
                    "op": "reinvented",
                    "name": existing.name,
                    "drift_field": field,
                    "expected": e_val,
                    "narrator": m_val,
                    "turn_number": turn_num,
                },
                component="npc_registry",
                severity="warning",
            )
