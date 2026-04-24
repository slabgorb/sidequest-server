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
    from sidequest.game.encounter import StructuredEncounter
    from sidequest.game.persistence import GameMode
    from sidequest.server.session_room import RoomRegistry, SessionRoom

from sidequest.agents.claude_client import ClaudeClient, ClaudeLike
from sidequest.agents.local_dm import LocalDM
from sidequest.agents.orchestrator import NpcMention, Orchestrator, TurnContext
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
from sidequest.game.persistence import SqliteStore, db_path_for_session
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
from sidequest.protocol import GameMessage, sanitize_player_text
from sidequest.protocol.dispatch import DispatchPackage
from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ConfrontationMessage,
    ConfrontationPayload,
    ErrorMessage,
    ErrorPayload,
    GamePausedMessage,
    GamePausedPayload,
    GameResumedMessage,
    ImageMessage,
    ImagePayload,
    NarrationEndMessage,
    NarrationEndPayload,
    NarrationMessage,
    NarrationPayload,
    PartyStatusMessage,
    PartyStatusPayload,
    PlayerPresenceMessage,
    PlayerPresencePayload,
    RenderQueuedMessage,
    RenderQueuedPayload,
    SeatConfirmedMessage,
    SeatConfirmedPayload,
    SecretNoteMessage,
    SecretNotePayload,
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
from sidequest.telemetry.spans import (
    SPAN_NPC_AUTO_REGISTERED,
    SPAN_NPC_REINVENTED,
    SPAN_ORCHESTRATOR_PROCESS_ACTION,  # noqa: F401 — re-exported for OTEL catalog consumers
    orchestrator_process_action_span,
)
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event-kind → message class mapping (MP-03 Task 3)
# Extend this dict as additional kinds are routed through _emit_event.
# ---------------------------------------------------------------------------

_KIND_TO_MESSAGE_CLS: dict[str, type] = {
    "NARRATION": NarrationMessage,
    "CONFRONTATION": ConfrontationMessage,
    "SECRET_NOTE": SecretNoteMessage,
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

    if kind == "CONFRONTATION":
        return message_cls(payload=ConfrontationPayload(**data))

    if kind == "SECRET_NOTE":
        return message_cls(payload=SecretNotePayload(**data))

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
    _HIDDEN_STATUS_TOKENS: frozenset[str] = frozenset({
        "hidden",
        "invisible",
        "stealth",
        "concealed",
    })

    @classmethod
    def _is_hidden_status_list(cls, statuses: list[str]) -> bool:
        return any(s.lower() in cls._HIDDEN_STATUS_TOKENS for s in statuses)

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
            emitter_player_id = (
                self._session_data.player_id if self._session_data else None
            )

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
                    for other_pid in room.connected_player_ids():
                        if other_pid == emitter_player_id:
                            continue
                        decision = projection_filter.project(
                            envelope=envelope, view=view, player_id=other_pid
                        )
                        if self._projection_cache is not None:
                            self._projection_cache.write_in_transaction(
                                event_seq=seq,
                                player_id=other_pid,
                                decision=decision,
                                conn=conn,
                            )
                        filtered_data: dict = {}
                        if decision.include:
                            filtered_data = json.loads(decision.payload_json)
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
                payload_cls = (
                    type(payload_model) if isinstance(payload_model, BaseModel) else None
                )
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
                            recipient_msg = message_cls(
                                payload={**filtered_data, "seq": seq}
                            )
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
            culture_ref = resolve_culture_reference(genre_pack, row.world_slug)
            world_context: str | None = culture_ref if culture_ref else None
            audio_backend = self._build_audio_backend(row.genre_slug, genre_pack)

            self._session_data = _SessionData(
                genre_slug=row.genre_slug,
                world_slug=row.world_slug,
                player_name=display_name,
                player_id=player_id,
                snapshot=snapshot,
                store=store,
                genre_pack=genre_pack,
                orchestrator=Orchestrator(client=self._client_factory()),
                local_dm=LocalDM(client=self._client_factory()),
                builder=builder,
                opening_seed=opening_seed,
                opening_directive=opening_directive,
                world_context=world_context,
                audio_backend=audio_backend,
                game_slug=slug,
                mode=GameMode(row.mode),
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
            replay_msgs: list[object] = []
            if self._projection_cache is not None:
                cached_rows = self._projection_cache.read_since(
                    player_id=self._current_player_id,
                    since_seq=self._last_seen_seq,
                )
                for c in cached_rows:
                    if not c.include or c.payload_json is None:
                        continue
                    # Need the event kind to rebuild the message — look it up.
                    # Most sessions won't have many missed events on reconnect,
                    # but this does one event-log read per cache row. Acceptable
                    # for v1; optimize to a join query if it becomes hot.
                    kind_lookup = self._event_log.read_since(since_seq=c.event_seq - 1)
                    if not kind_lookup or kind_lookup[0].seq != c.event_seq:
                        continue
                    replay_msgs.append(
                        _build_message_for_kind(
                            kind=kind_lookup[0].kind,
                            payload_json=c.payload_json,
                            seq=c.event_seq,
                        )
                    )
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
                    replay_msgs.append(
                        _build_message_for_kind(
                            kind=event_row.kind,
                            payload_json=dec.payload_json,
                            seq=event_row.seq,
                        )
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
                _bootstrap_tracer = trace.get_tracer(
                    "sidequest.server.session_handler"
                )
                with _bootstrap_tracer.start_as_current_span(
                    "slug_connect.chargen_bootstrap"
                ) as _bootstrap_span:
                    _bootstrap_span.set_attribute("player_id", player_id)
                    _bootstrap_span.set_attribute("slug", slug)
                    _bootstrap_span.set_attribute(
                        "scene_index", builder.current_scene_index()
                    )
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
                # / location update from the saved snapshot.
                if snapshot.characters:
                    try:
                        bootstrap_msgs.append(
                            self._build_session_start_party_status(
                                self._session_data, snapshot.characters[0], player_id
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "session.resume_party_status_failed error=%s", exc
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

        # Story 3.4 Task 12: strip [combat] markers from aside-flagged actions
        # before they reach the orchestrator (port of dispatch/aside.rs).
        if getattr(payload, "aside", False):
            from sidequest.server.dispatch.combat_brackets import (
                strip_combat_brackets,
            )
            action = strip_combat_brackets(action)
            if not action:
                return [_error_msg(
                    "Player aside is empty after combat-bracket strip"
                )]

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
        lore_context = await self._retrieve_lore_for_turn(sd, action)
        turn_context = _build_turn_context(sd, lore_context=lore_context)
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
                dispatch_package.degraded_reason, turn_id,
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
        # beat application / resolution happen in one place (the version
        # that emits the Story-3.4 OTEL spans the GM panel reads). The
        # develop-side ``apply_encounter_updates`` split was supplanted by
        # this richer combined helper; see merge commit for details.
        _apply_narration_result_to_snapshot(
            snapshot, result, sd.player_name, pack=sd.genre_pack,
        )
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
            and _is_combat_category(
                sd.genre_pack, snapshot.encounter.encounter_type
            )
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
                t.event_id, t.at,
            )

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

        # Group G Task 6: route prompt-redacted dispatches as SECRET_NOTE
        # events. Task 5's ``redact_dispatch_package`` stripped these from the
        # narrator prompt and parked them on ``result.secret_routes``; here we
        # reify each one as its own event so the same ProjectionFilter /
        # visibility_tag rule (Task 3) delivers it only to the recipients in
        # its ``_visibility.visible_to``. Only SubsystemDispatch entries route;
        # see ``build_secret_note_events`` for the skip rules.
        for _envelope in build_secret_note_events(
            result.secret_routes, turn_id=dispatch_package.turn_id,
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
        confrontation_msg: ConfrontationMessage | None = None
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
            confrontation_msg = ConfrontationMessage(
                payload=ConfrontationPayload(**payload_dict),
                player_id=sd.player_id,
            )
            trace.get_current_span().add_event(
                "confrontation.dispatched",
                {
                    "active": True,
                    "encounter_type": now_encounter.encounter_type,
                    "genre_slug": sd.genre_slug,
                },
            )
        elif prior_live and not now_live:
            from sidequest.server.dispatch.confrontation import (
                build_clear_confrontation_payload,
            )
            assert prior_type is not None  # guaranteed by prior_live=True
            payload_dict = build_clear_confrontation_payload(
                encounter_type=prior_type,
                genre_slug=sd.genre_slug,
            )
            confrontation_msg = ConfrontationMessage(
                payload=ConfrontationPayload(**payload_dict),
                player_id=sd.player_id,
            )
            trace.get_current_span().add_event(
                "confrontation.dispatched",
                {
                    "active": False,
                    "encounter_type": prior_type,
                    "genre_slug": sd.genre_slug,
                },
            )

        outbound: list[object] = [narration_msg]
        if confrontation_msg is not None:
            outbound.append(confrontation_msg)
        outbound.append(
            NarrationEndMessage(
                type="NARRATION_END",  # type: ignore[arg-type]
                payload=NarrationEndPayload(state_delta=None),
                player_id=sd.player_id,
            ),
        )

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

        lore_context = await self._retrieve_lore_for_turn(sd, action)
        turn_context = _build_turn_context(
            sd,
            opening_directive=sd.opening_directive,
            lore_context=lore_context,
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
            logger.warning(
                "audio.backend_skipped reason=pack_dir_missing genre=%s error=%s",
                genre_slug, exc,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "audio",
                    "op": "disabled",
                    "reason": "pack_dir_missing",
                    "genre": genre_slug,
                },
                component="audio",
            )
            return None

        audio_cfg = genre_pack.audio
        if not audio_cfg.mood_tracks and not audio_cfg.themes and not audio_cfg.sfx_library:
            logger.info(
                "audio.backend_skipped reason=empty_config genre=%s", genre_slug,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "audio",
                    "op": "disabled",
                    "reason": "empty_config",
                    "genre": genre_slug,
                },
                component="audio",
            )
            return None

        logger.info(
            "audio.backend_ready genre=%s pack_dir=%s",
            genre_slug, pack_dir,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "audio",
                "op": "enabled",
                "genre": genre_slug,
                "mood_count": len(audio_cfg.mood_tracks) + len(audio_cfg.themes),
                "sfx_count": len(audio_cfg.sfx_library),
            },
            component="audio",
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

        render_id = uuid.uuid4().hex[:12]
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
            },
            component="render",
        )

        out_queue = self._out_queue
        player_id = sd.player_id
        asyncio.create_task(
            self._run_render(client, params, render_id, out_queue, player_id)
        )

        return RenderQueuedMessage(
            type=MessageType.RENDER_QUEUED,  # type: ignore[arg-type]
            payload=RenderQueuedPayload(render_id=render_id),
            player_id=player_id,
        )

    # ------------------------------------------------------------------
    # Lore embedding — RAG retrieval (pre-turn) + worker dispatch (post-turn)
    # ------------------------------------------------------------------

    async def _retrieve_lore_for_turn(
        self, sd: _SessionData, action: str
    ) -> str | None:
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
            with tracer.start_as_current_span(
                "lore_embedding.dispatch_skipped"
            ) as skip_span:
                skip_span.set_attribute("lore.skip_reason", "worker_still_running")
                skip_span.set_attribute(
                    "lore.turn_number", sd.snapshot.turn_manager.interaction
                )
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
        sd.embed_task = asyncio.create_task(
            self._run_embed_worker(sd, len(pending), turn_number)
        )

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
        out_queue: asyncio.Queue[object],
        player_id: str,
    ) -> None:
        """Background render coroutine — waits for the daemon reply, then
        enqueues an IMAGE message or logs a failure. Never raises; any
        exception is caught and surfaced as an OTEL watcher event."""
        try:
            reply = await client.render(params)
        except DaemonUnavailableError as exc:
            logger.warning(
                "render.reply_unavailable render_id=%s error=%s", render_id, exc
            )
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
        served_url = _render_url_from_path(image_url)
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
        try:
            out_queue.put_nowait(msg)
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
            },
            component="render",
        )

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


def build_secret_note_events(
    removed: list,
    *,
    turn_id: str,
) -> list[MessageEnvelope]:
    """Build SECRET_NOTE envelopes from prompt-redacted dispatch entries.

    Group G Task 6. ``removed`` is the second element of the tuple returned
    by :func:`sidequest.agents.prompt_redaction.redact_dispatch_package`
    (also stashed on ``NarrationTurnResult.secret_routes``).

    Only ``SubsystemDispatch`` entries currently produce SECRET_NOTE events.
    ``NarratorDirective`` entries were never externally visible; their
    removal is already expressed by the narrator not mentioning the event.
    ``LethalityVerdict`` does not carry a VisibilityTag in the current
    protocol shape, so it falls through here too.

    ``origin_seq`` is ``0`` — the session handler's event-log append assigns
    the real seq at dispatch time (same pattern as NARRATION).
    """
    import json

    from sidequest.protocol.dispatch import SubsystemDispatch

    out: list[MessageEnvelope] = []
    for entry in removed:
        if not isinstance(entry, SubsystemDispatch):
            continue
        payload = {
            "turn_id": turn_id,
            "idempotency_key": entry.idempotency_key,
            "subsystem": entry.subsystem,
            "params": entry.params,
            "_visibility": {
                "visible_to": entry.visibility.visible_to,
                "fidelity": entry.visibility.perception_fidelity,
            },
        }
        out.append(MessageEnvelope(
            kind="SECRET_NOTE",
            payload_json=json.dumps(payload),
            origin_seq=0,
        ))
    return out


def emit_secret_notes(
    *,
    secret_routes: list,
    turn_id: str,
    event_log,
) -> None:
    """Log SECRET_NOTE events for every prompt-redacted dispatch on the turn.

    Consumes ``NarrationTurnResult.secret_routes`` (Task 5) and appends one
    SECRET_NOTE event per redacted ``SubsystemDispatch`` to the event log,
    so the same ProjectionFilter fan-out path that handles NARRATION will
    deliver each note to the recipients named in its ``_visibility.visible_to``.

    ``event_log`` is the session's :class:`sidequest.game.event_log.EventLog`
    (or a compatible fake in tests). Only the ``.append(kind, payload_json)``
    shape is used, which matches the real EventLog surface.
    """
    for envelope in build_secret_note_events(secret_routes, turn_id=turn_id):
        event_log.append(kind=envelope.kind, payload_json=envelope.payload_json)


def aggregate_visibility(pkg: DispatchPackage) -> dict:
    """Produce the _visibility sidecar for the canonical narration payload.

    Rules:
      - visible_to is the union of all non-redacted tags' visible_to lists.
      - "all" is a stop word — any "all" tag collapses the union to "all".
      - fidelity maps merge; later wins on collision (should not occur).
      - redacted events (redact_from_narrator_canonical=True) are NOT aggregated
        here — they route via SECRET_NOTE in Task 6 (not yet landed).
    """
    any_all = False
    union: set[str] = set()
    fidelity: dict[str, str] = {}
    for pd in pkg.per_player:
        for d in pd.dispatch:
            if d.visibility.redact_from_narrator_canonical:
                continue
            if d.visibility.visible_to == "all":
                any_all = True
            else:
                union.update(d.visibility.visible_to)
            fidelity.update(d.visibility.perception_fidelity)
    return {
        "visible_to": "all" if any_all else sorted(union),
        "fidelity": fidelity,
    }


def _build_turn_context(
    sd: _SessionData,
    *,
    opening_directive: str | None = None,
    lore_context: str | None = None,
) -> TurnContext:
    """Assemble the :class:`TurnContext` for a single narration turn.

    Shared by :meth:`_handle_player_action` and the opening-turn
    bootstrap (Slice H). ``opening_directive`` is consumed by the
    narrator on turn 0 only — the caller is responsible for clearing
    the session-level directive after the turn runs. ``lore_context``
    (story 37-33) is the pre-rendered ``<lore>`` block from
    :func:`sidequest.game.lore_embedding.retrieve_lore_context`;
    ``None`` means no lore section is registered on this turn.
    """
    from sidequest.agents.encounter_render import render_encounter_summary
    from sidequest.server.dispatch.confrontation import find_confrontation_def

    snapshot = sd.snapshot
    char_name = (
        snapshot.characters[0].core.name if snapshot.characters else sd.player_name
    )

    # Derive encounter flags from snapshot.encounter (Story 3.4).
    # Uses category-based flags from the matched ConfrontationDef
    # (combat / movement) rather than string-matching on encounter_type,
    # and skips resolved encounters so a just-closed combat doesn't keep
    # flipping in_combat=True.
    encounter = snapshot.encounter
    confrontation_def = None
    encounter_summary = None
    in_combat = False
    in_chase = False
    in_encounter = False
    if encounter is not None and not encounter.resolved:
        in_encounter = True
        defs = sd.genre_pack.rules.confrontations if sd.genre_pack.rules else []
        confrontation_def = find_confrontation_def(defs, encounter.encounter_type)
        if confrontation_def is not None:
            in_combat = confrontation_def.category == "combat"
            in_chase = confrontation_def.category == "movement"
        encounter_summary = render_encounter_summary(encounter)

    return TurnContext(
        in_combat=in_combat,
        in_chase=in_chase,
        in_encounter=in_encounter,
        encounter=encounter if in_encounter else None,
        confrontation_def=confrontation_def,
        encounter_summary=encounter_summary,
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
        lore_context=lore_context,
    )


def _find_confrontation_def(pack: GenrePack, confrontation_type: str) -> object | None:
    """Look up the ConfrontationDef matching the narrator's hint.

    Returns ``None`` when the pack doesn't declare that confrontation
    type; the caller skips encounter context injection and the narrator
    will run without beats (narration-only fallback).
    """
    rules = getattr(pack, "rules", None)
    if rules is None:
        return None
    confrontations = getattr(rules, "confrontations", None) or []
    for cd in confrontations:
        if getattr(cd, "confrontation_type", None) == confrontation_type:
            return cd
    return None


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
    *,
    pack: GenrePack | None = None,
) -> None:
    """Apply game_patch extracted fields from NarrationTurnResult to the snapshot.

    Phase 1: location, quest_updates, lore_established, npc_registry updates.
    Story 3.4: encounter instantiation and beat application (when pack provided).
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

    # --- Encounter lifecycle (Story 3.4) ---
    if pack is not None:
        from sidequest.game.encounter import EncounterPhase, MetricDirection
        from sidequest.server.dispatch.confrontation import find_confrontation_def
        from sidequest.server.dispatch.encounter_lifecycle import (
            instantiate_encounter_from_trigger,
        )
        from sidequest.telemetry.spans import (
            combat_tick_span,
            encounter_beat_applied_span,
            encounter_phase_transition_span,
            encounter_resolved_span,
        )

        # (a) Narrator-initiated encounter
        if result.confrontation and (
            snapshot.encounter is None or snapshot.encounter.resolved
        ):
            combatants = [e.name for e in result.npcs_present] or [player_name]
            combatants = [player_name] + [c for c in combatants if c != player_name]
            instantiate_encounter_from_trigger(
                snapshot=snapshot,
                pack=pack,
                encounter_type=result.confrontation,
                combatants=combatants,
                hp=10,
                genre_slug=snapshot.genre_slug,
            )

        # (b) Apply beat_selections
        enc = snapshot.encounter
        if enc is not None and not enc.resolved and result.beat_selections:
            cdef = find_confrontation_def(
                pack.rules.confrontations if pack.rules else [],
                enc.encounter_type,
            )
            if cdef is None:
                raise ValueError(
                    f"active encounter type {enc.encounter_type!r} not in pack"
                )
            beat_by_id = {b.id: b for b in cdef.beats}
            prev_phase = enc.structured_phase
            for sel in result.beat_selections:
                beat = beat_by_id.get(sel.beat_id)
                if beat is None:
                    raise ValueError(
                        f"unknown beat_id {sel.beat_id!r} for encounter "
                        f"{enc.encounter_type!r}"
                    )
                with encounter_beat_applied_span(
                    encounter_type=enc.encounter_type,
                    actor=sel.actor,
                    beat_id=sel.beat_id,
                    metric_delta=beat.metric_delta,
                ):
                    enc.metric.current += beat.metric_delta
                    # Ascending metrics clamp at 0 (port of Rust encounter.rs).
                    if (
                        enc.metric.direction == MetricDirection.Ascending
                        and enc.metric.current < 0
                    ):
                        enc.metric.current = 0
                enc.beat += 1
                _advance_phase(enc)
                with combat_tick_span(
                    encounter_type=enc.encounter_type,
                    beat=enc.beat,
                    phase=(enc.structured_phase or EncounterPhase.Setup).value,
                ):
                    pass
                # Direction-aware threshold resolution (port of Rust encounter.rs):
                # Ascending fires on high only; Descending on low only; Bidirectional
                # on either. Cross-checking the wrong boundary would falsely resolve
                # a chase the moment its counter dipped below zero.
                m = enc.metric
                if m.direction == MetricDirection.Ascending:
                    threshold_hit = (
                        m.threshold_high is not None and m.current >= m.threshold_high
                    )
                elif m.direction == MetricDirection.Descending:
                    threshold_hit = (
                        m.threshold_low is not None and m.current <= m.threshold_low
                    )
                else:  # Bidirectional
                    threshold_hit = (
                        (m.threshold_high is not None and m.current >= m.threshold_high)
                        or (m.threshold_low is not None and m.current <= m.threshold_low)
                    )
                if threshold_hit or beat.resolution:
                    enc.resolved = True
                    enc.structured_phase = EncounterPhase.Resolution
                    enc.outcome = f"resolved at beat {enc.beat}"
                    with encounter_resolved_span(
                        encounter_type=enc.encounter_type,
                        outcome=enc.outcome,
                        source="metric",
                    ):
                        pass
                    break
            if prev_phase != enc.structured_phase:
                with encounter_phase_transition_span(
                    from_phase=(prev_phase.value if prev_phase else "None"),
                    to_phase=(enc.structured_phase.value
                              if enc.structured_phase else "None"),
                    encounter_type=enc.encounter_type,
                ):
                    pass


def _advance_phase(enc: StructuredEncounter) -> None:
    """Promote encounter phase by beat count. Port of Rust encounter.rs ladder."""
    from sidequest.game.encounter import EncounterPhase
    if enc.structured_phase is None:
        enc.structured_phase = EncounterPhase.Setup
    ladder = {
        0: EncounterPhase.Setup,
        1: EncounterPhase.Opening,
        2: EncounterPhase.Escalation,
        3: EncounterPhase.Escalation,
        4: EncounterPhase.Escalation,
    }
    enc.structured_phase = ladder.get(enc.beat, EncounterPhase.Climax)


def apply_encounter_updates(
    snapshot: GameSnapshot,
    result: object,
    genre_pack: GenrePack,
    player_name: str,
) -> None:
    """Materialize, advance, and resolve encounter state from narrator output.

    Three cases, in order:

    1. No encounter active + narrator hinted a confrontation type: look up
       the matching :class:`ConfrontationDef` in ``genre_pack.rules`` and
       instantiate a :class:`StructuredEncounter` from the def's metric +
       actor list (player + recently-mentioned hostile NPCs). Writes to
       ``snapshot.encounter``.
    2. Encounter active + narrator emitted ``beat_selections``: for each
       selection, find the matching :class:`BeatDef` and apply its
       ``metric_delta`` to the encounter's metric. Resolution beats (and
       any metric crossing a threshold) mark ``encounter.resolved`` and
       clear ``snapshot.encounter``.
    3. Encounter active + narrator hinted a new confrontation type: no-op.
       The narrator should not be starting a second encounter while one
       is running; we trust the existing state.

    Each step emits a ``state_transition`` watcher event tagged
    ``component=encounter`` so the dashboard Subsystems tab surfaces the
    mechanical loop Sebastien wants to see.
    """
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.game.encounter import (
        EncounterActor,
        EncounterMetric,
        EncounterPhase,
        MetricDirection,
        StructuredEncounter,
    )

    if not isinstance(result, NarrationTurnResult):
        return

    confrontation_hint = result.confrontation
    turn_num = snapshot.turn_manager.interaction

    # Case 1 — start a new encounter.
    if snapshot.encounter is None and confrontation_hint:
        conf_def = _find_confrontation_def(genre_pack, confrontation_hint)
        if conf_def is None:
            logger.warning(
                "encounter.skipped reason=no_matching_def type=%s player=%s",
                confrontation_hint,
                player_name,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "skipped",
                    "reason": "no_matching_def",
                    "confrontation_type": confrontation_hint,
                },
                component="encounter",
                severity="warning",
            )
            return
        # Build actors from the player character and any hostile NPCs the
        # narrator referenced this turn. Keeps per-turn state simple —
        # tactical grid is Phase 4. Hostile-role detection is a rough
        # heuristic (anything whose role contains "combat", "hostile",
        # "enemy", or a named role like "bandit"/"creature"); the
        # narrator also gets a chance to refine this via later turns.
        actors: list[EncounterActor] = []
        if snapshot.characters:
            player_actor_name = snapshot.characters[0].core.name or player_name
        else:
            player_actor_name = player_name
        actors.append(
            EncounterActor(name=player_actor_name, role="player", per_actor_state={})
        )
        hostile_keywords = {"combat", "hostile", "enemy", "combatant"}
        for npc in result.npcs_present or []:
            role = (npc.role or "").lower()
            if any(k in role for k in hostile_keywords) or role in {"brood-mother", "predator"}:
                actors.append(
                    EncounterActor(name=npc.name, role="combatant", per_actor_state={})
                )
        # Convert the pack metric def into the live EncounterMetric.
        md = conf_def.metric
        direction_map = {
            "ascending": MetricDirection.Ascending,
            "descending": MetricDirection.Descending,
            "bidirectional": MetricDirection.Bidirectional,
        }
        metric = EncounterMetric(
            name=md.name,
            current=md.starting,
            starting=md.starting,
            direction=direction_map.get(md.direction, MetricDirection.Bidirectional),
            threshold_high=md.threshold_high,
            threshold_low=md.threshold_low,
        )
        snapshot.encounter = StructuredEncounter(
            encounter_type=conf_def.confrontation_type,
            metric=metric,
            beat=0,
            structured_phase=EncounterPhase.Setup,
            actors=actors,
            outcome=None,
            resolved=False,
            mood_override=conf_def.mood,
            narrator_hints=[],
        )
        logger.info(
            "encounter.started type=%s metric=%s=%d actors=%d player=%s",
            conf_def.confrontation_type,
            metric.name,
            metric.current,
            len(actors),
            player_name,
        )
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "started",
                "confrontation_type": conf_def.confrontation_type,
                "metric_name": metric.name,
                "metric_current": metric.current,
                "actors": [a.name for a in actors],
                "turn_number": turn_num,
            },
            component="encounter",
        )

    # Case 2 — advance an active encounter via beat_selections.
    if snapshot.encounter is not None and result.beat_selections:
        conf_def = _find_confrontation_def(
            genre_pack, snapshot.encounter.encounter_type
        )
        if conf_def is None:
            return
        beat_lookup = {b.id: b for b in conf_def.beats}
        resolved_this_turn = False
        for selection in result.beat_selections:
            beat_id = getattr(selection, "beat_id", None) or ""
            actor = getattr(selection, "actor", None) or ""
            beat_def = beat_lookup.get(beat_id)
            if beat_def is None:
                logger.warning(
                    "encounter.beat_skipped reason=unknown_beat_id beat_id=%r actor=%r",
                    beat_id,
                    actor,
                )
                _watcher_publish(
                    "state_transition",
                    {
                        "field": "encounter",
                        "op": "beat_skipped",
                        "reason": "unknown_beat_id",
                        "beat_id": beat_id,
                        "actor": actor,
                    },
                    component="encounter",
                    severity="warning",
                )
                continue
            before = snapshot.encounter.metric.current
            snapshot.encounter.metric.current += int(beat_def.metric_delta or 0)
            snapshot.encounter.beat += 1
            logger.info(
                "encounter.beat_applied beat=%s actor=%s metric=%s %d->%d",
                beat_id,
                actor,
                snapshot.encounter.metric.name,
                before,
                snapshot.encounter.metric.current,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "beat_applied",
                    "beat_id": beat_id,
                    "actor": actor,
                    "metric_before": before,
                    "metric_after": snapshot.encounter.metric.current,
                    "metric_delta": beat_def.metric_delta,
                    "turn_number": turn_num,
                },
                component="encounter",
            )
            if beat_def.resolution:
                resolved_this_turn = True
                snapshot.encounter.outcome = beat_def.id
        # Threshold crossing — resolve the encounter.
        metric = snapshot.encounter.metric
        hit_high = (
            metric.threshold_high is not None
            and metric.current >= metric.threshold_high
        )
        hit_low = (
            metric.threshold_low is not None
            and metric.current <= metric.threshold_low
        )
        if resolved_this_turn or hit_high or hit_low:
            etype = snapshot.encounter.encounter_type
            outcome = (
                snapshot.encounter.outcome
                or ("threshold_high" if hit_high else "threshold_low" if hit_low else "resolved")
            )
            logger.info(
                "encounter.resolved type=%s outcome=%s final_metric=%d",
                etype,
                outcome,
                metric.current,
            )
            _watcher_publish(
                "state_transition",
                {
                    "field": "encounter",
                    "op": "resolved",
                    "confrontation_type": etype,
                    "outcome": outcome,
                    "final_metric": metric.current,
                    "turn_number": turn_num,
                },
                component="encounter",
            )
            snapshot.encounter = None


def _render_url_from_path(image_path: str) -> str:
    """Translate a daemon-returned filesystem path into a URL the UI can
    fetch via the server's ``/renders/*`` static mount.

    The daemon writes every image under ``SIDEQUEST_OUTPUT_DIR`` (or a
    tempdir when the env var is unset). The server mounts that same
    directory at ``/renders``, so the mapping is purely a prefix swap.
    When the path isn't inside that root (tempdir the server can't see),
    the absolute path is returned verbatim; the UI will fail to load the
    image, but a clear 404 is better than a silent replacement.
    """
    import os as _os
    import pathlib as _pathlib

    root = _os.environ.get("SIDEQUEST_OUTPUT_DIR")
    if not root or not image_path:
        return image_path
    try:
        rel = _pathlib.Path(image_path).resolve().relative_to(
            _pathlib.Path(root).resolve()
        )
    except ValueError:
        return image_path
    return "/renders/" + str(rel).replace(_os.sep, "/")


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
