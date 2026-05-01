"""Per-connection WebSocket session handler.

Extracted from ``session_handler.py``: the ``WebSocketSessionHandler`` class
lives here so the lifecycle/dispatch surface can evolve without churning the
helpers (``_State``, ``_SessionData``, ``_KIND_TO_MESSAGE_CLS``, etc.) that
remain re-exported from ``session_handler``. Tracer name is preserved
(``sidequest.server.session_handler``) so OTEL consumers do not see a
span-source rename.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from sidequest.handlers.base import MessageHandler
    from sidequest.server.session_room import RoomRegistry, SessionRoom

from sidequest.agents.claude_client import ClaudeClient, LlmClient
from sidequest.agents.orchestrator import TurnContext
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
from sidequest.game.lore_seeding import seed_lore_from_char_creation
from sidequest.game.projection.cache import ProjectionCache
from sidequest.game.projection_filter import ProjectionFilter
from sidequest.game.region_init import RegionInitError, init_region_location
from sidequest.game.room_movement import (
    RoomGraphInitError,
    init_room_graph_location,
)
from sidequest.game.session import (
    GameSnapshot,
    NarrativeEntry,
)
from sidequest.game.shared_world_delta import (
    build_shared_world_delta,
)
from sidequest.game.world_materialization import (
    CampaignMaturity,
    HistoryParseError,
    materialize_from_genre_pack,
    parse_history_chapters,
    recompute_arc_history,
    should_recompute_arc,
)
from sidequest.genre.archetype.shim import resolve_archetype
from sidequest.genre.error import GenreValidationError
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS, GenreLoader
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.world import NavigationMode
from sidequest.protocol import GameMessage
from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import (
    AudioCueMessage,
    AudioCuePayload,
    ChapterMarkerMessage,
    ChapterMarkerPayload,
    CharacterCreationMessage,
    CharacterCreationPayload,
    ConfrontationPayload,
    ImageMessage,
    ImagePayload,
    NarrationEndMessage,
    NarrationEndPayload,
    NarrationMessage,
    NarrationPayload,
    RenderQueuedMessage,
    RenderQueuedPayload,
    ScrapbookEntryPayload,
    SecretNotePayload,
    SessionEventPayload,
    TurnStatusMessage,
    TurnStatusPayload,
)
from sidequest.protocol.models import (
    Footnote,
)
from sidequest.protocol.types import NonBlankString
from sidequest.server import views
from sidequest.server.audio_cue import build_audio_cue_payload
from sidequest.server.dispatch.chargen_loadout import apply_starting_loadout
from sidequest.server.dispatch.chargen_summary import render_confirmation_summary
from sidequest.server.dispatch.scenario_bind import bind_scenario
from sidequest.server.magic_init import init_magic_state_for_session
from sidequest.server.narration_apply import (
    _apply_narration_result_to_snapshot,
    _handshake_resolved_tropes,
)
from sidequest.server.session_handler import (
    _AUDIO_INTERPRETER,
    _build_pc_descriptor,
    _hash_snapshot,
    _SessionData,
    _shared_world_delta_to_state_delta,
    _State,
)
from sidequest.server.session_helpers import (
    _build_turn_context,
    _error_msg,
    _render_url_from_path,
    _resolve_acting_character_name,
    _resolve_location_display,
    _world_history_value,
    build_secret_note_events,
)
from sidequest.server.utils import slugify_player_name as _slugify_player_name
from sidequest.telemetry.phase_timing import PhaseTimings
from sidequest.telemetry.spans import (
    SPAN_CHARGEN_ARCHETYPE_GATE_BLOCKED,
    SPAN_CHARGEN_ARCHETYPE_GATE_EVALUATED,
    audio_backend_disabled_span,
    audio_backend_enabled_span,
    audio_dispatched_span,
    audio_skipped_span,
    encounter_momentum_broadcast_span,
    orchestrator_process_action_span,
    round_invariant_span,
    turn_span,
)
from sidequest.telemetry.turn_record import PatchSummary, TurnRecord
from sidequest.telemetry.validator import Validator
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

logger = logging.getLogger(__name__)

# Preserve the original tracer name so OTEL span sources do not rename when
# this class moved out of session_handler.py. Phase-3 plan principle.
tracer = trace.get_tracer("sidequest.server.session_handler")


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

    # ------------------------------------------------------------------
    # EventLog fan-out helper (MP-03 Task 3)
    # ------------------------------------------------------------------

    def _emit_event(self, kind: str, payload_model: object) -> object:
        """Persist + fan-out an event. Delegates to ``emitters.emit_event``.

        Phase 1 of session_handler decomposition (see
        docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).
        """
        from sidequest.server import emitters

        return emitters.emit_event(self, kind, payload_model)

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
        """Persist + emit a scrapbook entry. Delegates to ``emitters.emit_scrapbook_entry``.

        Phase 1 of session_handler decomposition (see
        docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).
        """
        from sidequest.server import emitters

        emitters.emit_scrapbook_entry(self, sd=sd, snapshot=snapshot, result=result)

    def _persist_scrapbook_entry(self, payload: ScrapbookEntryPayload) -> None:
        """Insert a scrapbook row. Delegates to ``emitters.persist_scrapbook_entry``.

        Phase 1 of session_handler decomposition (see
        docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).
        """
        from sidequest.server import emitters

        emitters.persist_scrapbook_entry(self, payload)

    # ------------------------------------------------------------------
    # Public entrypoints
    # ------------------------------------------------------------------

    @property
    def session_data(self) -> _SessionData | None:
        """Public read accessor for session state (used by tests and GM panel)."""
        return self._session_data

    async def handle_message(self, msg: GameMessage) -> list[object]:
        """Dispatch an inbound message; return list of outbound protocol message objects.

        Looks the message type up in the per-class ``_MESSAGE_HANDLERS`` registry
        (built once at first dispatch) and forwards to the corresponding
        first-class handler under :mod:`sidequest.handlers`. The thin
        ``_handle_X`` methods on this class remain as a test-friendly
        public API so test suites can drive a single message type without
        going through the WebSocket protocol layer.
        """
        msg_type: str = msg.type  # type: ignore[attr-defined]

        handler = type(self)._message_handler_for(msg_type)
        if handler is None:
            logger.warning(
                "session.unhandled_message_type type=%s state=%s",
                msg_type,
                self._state.name,
            )
            return [_error_msg(f"Unsupported message type in Phase 1: {msg_type}")]
        return await handler.handle(self, msg)

    @classmethod
    def _message_handler_for(cls, msg_type: str) -> MessageHandler | None:
        """Lazy-built registry of message-type → first-class handler singleton.

        Built on first call to avoid importing the handler modules at
        ``WebSocketSessionHandler`` class-definition time, which would
        eagerly drag in their transitive imports (and create a circular
        reference, since the handler modules import this class for
        type-checking). The registry is cached on the class so subsequent
        dispatches are a single dict lookup.
        """
        registry = getattr(cls, "_MESSAGE_HANDLERS", None)
        if registry is None:
            from sidequest.handlers.character_creation import HANDLER as CHARACTER_CREATION_HANDLER
            from sidequest.handlers.dice_throw import HANDLER as DICE_THROW_HANDLER
            from sidequest.handlers.player_action import HANDLER as PLAYER_ACTION_HANDLER
            from sidequest.handlers.player_seat import HANDLER as PLAYER_SEAT_HANDLER
            from sidequest.handlers.session_event import HANDLER as SESSION_EVENT_HANDLER
            from sidequest.handlers.yield_action import HANDLER as YIELD_HANDLER

            registry = {
                "SESSION_EVENT": SESSION_EVENT_HANDLER,
                "PLAYER_ACTION": PLAYER_ACTION_HANDLER,
                "CHARACTER_CREATION": CHARACTER_CREATION_HANDLER,
                "PLAYER_SEAT": PLAYER_SEAT_HANDLER,
                "DICE_THROW": DICE_THROW_HANDLER,
                "YIELD": YIELD_HANDLER,
            }
            cls._MESSAGE_HANDLERS = registry
        return registry.get(msg_type)

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
                    with contextlib.suppress(Exception):
                        self._session_data.store.close()

    # ------------------------------------------------------------------
    # PLAYER_SEAT dispatch (MP-02 Task 5)
    # ------------------------------------------------------------------

    async def _handle_player_seat(self, msg: GameMessage) -> list[object]:
        """Handle PLAYER_SEAT — delegates to ``sidequest.handlers.player_seat.HANDLER``."""
        from sidequest.handlers.player_seat import HANDLER

        return await HANDLER.handle(self, msg)

    # ------------------------------------------------------------------
    # DICE_THROW dispatch (story 34 port — restored for 2026-04-24 playtest)
    # ------------------------------------------------------------------

    async def _handle_dice_throw(self, msg: GameMessage) -> list[object]:
        """Handle DICE_THROW — delegates to ``sidequest.handlers.dice_throw.HANDLER``."""
        from sidequest.handlers.dice_throw import HANDLER

        return await HANDLER.handle(self, msg)

    # ------------------------------------------------------------------
    # YIELD dispatch (dual-track momentum Phase 3)
    # ------------------------------------------------------------------

    async def _handle_yield(self, msg: GameMessage) -> list[object]:
        """Handle YIELD — delegates to ``sidequest.handlers.yield_action.HANDLER``."""
        from sidequest.handlers.yield_action import HANDLER

        return await HANDLER.handle(self, msg)

    # ------------------------------------------------------------------
    # SESSION_EVENT dispatch
    # ------------------------------------------------------------------

    async def _handle_session_event(self, msg: GameMessage) -> list[object]:
        """Handle SESSION_EVENT — delegates to ``sidequest.handlers.session_event.HANDLER``."""
        from sidequest.handlers.session_event import HANDLER

        return await HANDLER.handle(self, msg)

    async def _handle_connect(
        self,
        payload: SessionEventPayload,
        player_id: str,
    ) -> list[object]:
        """Handle connect sub-event — delegates to ``sidequest.handlers.connect.HANDLER``."""
        from sidequest.handlers.connect import HANDLER

        return await HANDLER.handle(self, payload, player_id)

    # ------------------------------------------------------------------
    # CHARACTER_CREATION dispatch
    # ------------------------------------------------------------------

    async def _handle_character_creation(self, msg: GameMessage) -> list[object]:
        """Handle CHARACTER_CREATION — delegates to ``sidequest.handlers.character_creation.HANDLER``."""
        from sidequest.handlers.character_creation import HANDLER

        return await HANDLER.handle(self, msg)

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
        ``builder.py:1585-1590``). This helper detects that raw form, runs
        the four-tier resolve (base → constraints → world funnels), and
        replaces the raw pair with the resolved display name via
        ``apply_archetype_resolved`` — keeping ``archetype_provenance`` in
        lockstep.

        Resolution failures emit a ``character_creation.archetype_resolution_failed``
        span event and leave the raw pair in place. The downstream
        archetype-resolution gate in ``_chargen_confirmation`` (Story
        45-6, ``_gate_archetype_resolution``) detects the partial state
        and rejects the commit with a typed ERROR frame
        (``code="chargen_archetype_unresolved"``); this helper is
        no-op-on-failure intentionally so the gate can decide. Missing
        axis data on the pack (no ``base_archetypes`` or
        ``archetype_constraints``) also silently no-ops here — the
        gate then routes to ``OK_NO_AXES`` (pass) if the builder
        produced no pair, or ``raw_pair_unresolved`` (block) if the
        builder produced a pair the resolver couldn't run.

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

    # ---- archetype-resolution gate (Story 45-6) -------------------------
    def _gate_archetype_resolution(
        self,
        character: Character,
        sd: _SessionData,
        player_id: str,
        span: trace.Span,
    ) -> tuple[bool, str | None]:
        """Inspect the post-resolve character state and decide whether
        chargen is allowed to ship.

        The gate distinguishes three states:

        - **OK_RESOLVED** — ``apply_archetype_resolved`` ran and stamped
          ``archetype_provenance``. The discriminator keys on
          ``character.archetype_provenance is not None``, NOT on the
          shape of ``resolved_archetype`` — that survives display
          names that legitimately contain ``"/"``. Pass.
        - **OK_NO_AXES** — ``resolved_archetype is None`` AND the pack
          opted out of the archetype system
          (``base_archetypes is None and archetype_constraints is None``).
          The builder didn't form a pair and the resolver had nothing
          to resolve. Pass.
        - **BLOCKED_PARTIAL** — anything else. Three failure modes
          (Story 45-6 / playtest 3 ``pumblestone`` corpus):

          1. ``raw_pair_unresolved`` — a literal ``"jungian/rpg_role"``
             string is still on the character AND the pack lacks axes.
             ``_resolve_character_archetype`` short-circuited at the
             pack-axes check (line 579) before calling
             ``resolve_archetype``; the raw pair stayed.
          2. ``missing_axes_with_pack_axes`` — pack declares axes but
             the chargen scenes accumulated at most one hint, so the
             builder set ``resolved_archetype = None``. This is the
             ``pumblestone`` failure: chargen scenes malformed.
          3. ``resolver_raised`` — pack has axes AND a raw pair is
             still on the character. Pure shape inference: the
             pack-lacks-axes short-circuit at line 579 would have
             returned before calling ``resolve_archetype``, so a raw
             pair with pack-axes-set can only mean the resolver was
             called and raised — the catch-block at line 595 caught
             ``GenreValidationError`` and returned, leaving the raw
             pair.

        The evaluator span fires on every chargen-confirm; the blocked
        span fires only on BLOCKED_PARTIAL. Both go to the GM panel via
        ``SPAN_ROUTES`` (Sebastien's lie-detector — CLAUDE.md OTEL
        Observability Principle). On the blocked branch a
        ``logger.warning()`` entry is also emitted to the structured
        server log so ops debugging is independent of the OTEL pipeline
        (python.md rule 4).

        Returns ``(is_blocked, block_reason)`` — ``block_reason`` is
        one of ``"raw_pair_unresolved"``,
        ``"missing_axes_with_pack_axes"``, ``"resolver_raised"``, or
        ``None`` on a pass.
        """
        pack = sd.genre_pack
        pack_has_axes = pack.base_archetypes is not None and pack.archetype_constraints is not None
        ra = character.resolved_archetype
        provenance_set = character.archetype_provenance is not None
        # ``had_both_hints`` is the only granularity the gate has access
        # to: the builder writes ``resolved_archetype = f"{j}/{r}"`` iff
        # both hints were set, else ``None``. After the resolver runs,
        # ``apply_archetype_resolved`` may overwrite the raw pair with a
        # display name — but ``apply_archetype_resolved`` only fires
        # when both hints fed the resolver in the first place. So:
        # ``ra is not None`` ⇒ both hints; ``ra is None`` ⇒ at most one
        # hint. The gate cannot tell *which* hint was missing without
        # threading the builder accumulator through, which would be
        # invasive — the OTEL signal is intentionally per-pair, not
        # per-axis.
        had_both_hints = ra is not None

        # Decide. The discriminator keys on ``provenance_set`` (the
        # OK_RESOLVED signal) rather than ``"/"`` in ``ra`` — the latter
        # would misclassify display names that legitimately contain
        # ``"/"`` (no validator on ``ArchetypeResolved.name`` forbids
        # it). ``apply_archetype_resolved`` writes both
        # ``resolved_archetype`` and ``archetype_provenance`` together,
        # so ``provenance_set`` is the durable lockstep signal.
        gate_state: str
        block_reason: str | None
        if provenance_set:
            # OK_RESOLVED — resolver succeeded and stamped provenance.
            gate_state = "ok_resolved"
            block_reason = None
        elif ra is None and not pack_has_axes:
            # OK_NO_AXES — pack opted out of the archetype system.
            gate_state = "ok_no_axes"
            block_reason = None
        elif ra is None:
            # ra is None AND pack_has_axes — pumblestone case (chargen
            # scenes malformed; the builder didn't form a pair).
            block_reason = "missing_axes_with_pack_axes"
            gate_state = "blocked_partial"
        else:
            # Raw pair on the character (no provenance ⇒ resolver did
            # not run successfully). With pack-axes-set the resolver
            # was called and raised; without pack-axes it short-
            # circuited at line 579.
            block_reason = "resolver_raised" if pack_has_axes else "raw_pair_unresolved"
            gate_state = "blocked_partial"

        # Evaluator span — fires on every chargen-confirm. ``state``
        # carries the decision so the GM panel sees the choice on every
        # path, including the success branches (negative confirmation
        # that the gate ran).
        with tracer.start_as_current_span(
            SPAN_CHARGEN_ARCHETYPE_GATE_EVALUATED,
            attributes={
                "state": gate_state,
                "resolved_archetype": ra if ra is not None else "",
                "pack_has_axes": pack_has_axes,
                "had_both_hints": had_both_hints,
                "provenance_set": provenance_set,
                "genre": sd.genre_slug,
                "world": sd.world_slug,
                "player_id": player_id,
            },
        ):
            pass

        if block_reason is None:
            return False, None

        # python.md rule 4: error paths MUST log to the structured
        # server log surface. The OTEL span is independent (it goes to
        # the watcher dashboard / GM panel); this WARNING entry lands
        # in journald / file logs so ops debugging works without the
        # OTEL pipeline.
        logger.warning(
            "chargen.archetype_gate_blocked player_id=%s block_reason=%s "
            "genre=%s world=%s pack_has_axes=%s resolved_archetype=%s",
            player_id,
            block_reason,
            sd.genre_slug,
            sd.world_slug,
            pack_has_axes,
            ra if ra is not None else "<none>",
        )

        # Blocked span — fires only on BLOCKED_PARTIAL. This is the
        # explicit lie-detector entry that says "a chargen would have
        # shipped broken; the gate caught it." The legacy
        # ``character_creation.archetype_resolution_failed`` event is
        # the inner-resolver event; the blocked span is the outer-gate
        # event.
        with tracer.start_as_current_span(
            SPAN_CHARGEN_ARCHETYPE_GATE_BLOCKED,
            attributes={
                "state": "blocked_partial",
                "block_reason": block_reason,
                "resolved_archetype": ra if ra is not None else "",
                "pack_has_axes": pack_has_axes,
                "had_both_hints": had_both_hints,
                "provenance_set": provenance_set,
                "genre": sd.genre_slug,
                "world": sd.world_slug,
                "player_id": player_id,
            },
        ) as gate_span:
            # Also record on the parent span for correlation in the
            # existing ``character_creation.*`` event stream.
            span.add_event(
                "character_creation.archetype_gate_blocked",
                {
                    "event": "archetype_gate_blocked",
                    "block_reason": block_reason,
                    "resolved_archetype": ra if ra is not None else "",
                    "pack_has_axes": pack_has_axes,
                    "player_id": player_id,
                },
            )
            # Mirror onto the gate span as well so ReadableSpan
            # consumers (test exporter) see the same payload either
            # way.
            gate_span.add_event(
                "character_creation.archetype_gate_blocked",
                {"block_reason": block_reason},
            )

        return True, block_reason

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

        # Story 45-6: archetype-resolution gate. After resolution runs
        # (or silently no-ops via one of the three early-return branches
        # at lines 574, 579, 595 of ``_resolve_character_archetype``),
        # this gate inspects the post-state and rejects partial
        # commits — the ``pumblestone`` regression from Playtest 3
        # evrópí. See ``_gate_archetype_resolution`` docstring for the
        # three pass / fail paths.
        is_blocked, block_reason = self._gate_archetype_resolution(character, sd, player_id, span)
        if is_blocked:
            return [
                _error_msg(
                    "Character creation incomplete: archetype resolution "
                    f"failed ({block_reason}). Please re-run chargen.",
                    code="chargen_archetype_unresolved",
                )
            ]

        # Starting equipment loadout (Story 2.3 Slice A). The builder only
        # holds item_hints; the class-specific loadout from inventory.yaml
        # is wired in here. Rust parity: connect.rs:1745-1864.
        # Story 45-12: pass session identity so the dedup-evaluated /
        # dedup-fired spans carry genre/world/player_id for GM-panel
        # attribution. Snapshot slugs are populated at connect time.
        apply_starting_loadout(
            character,
            sd.genre_pack.inventory,
            genre=sd.snapshot.genre_slug,
            world=sd.snapshot.world_slug,
            player_id=player_id,
        )

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
            history_value = _world_history_value(sd.genre_pack, sd.world_slug)
            try:
                # Story 45-19: parse once at chargen and cache the typed
                # chapter list on _SessionData so the per-turn arc-recompute
                # tick doesn't re-parse history.yaml on every interaction.
                # Same input drives ``materialize_from_genre_pack`` below;
                # both calls share the same HistoryParseError fate.
                sd.cached_history_chapters = parse_history_chapters(history_value)
                materialized = materialize_from_genre_pack(
                    history_value,
                    CampaignMaturity.Fresh,
                    sd.genre_slug,
                    sd.world_slug,
                )
            except HistoryParseError as exc:
                # Loud failure (CLAUDE.md "No Silent Fallbacks"): log at
                # ERROR and emit an OTEL span event so the GM panel /
                # watcher dashboard surfaces the malformed shipping
                # content. We retain the empty-snapshot fallback because
                # the character has already been chargen-built and
                # hard-failing here would orphan the commit; but the
                # error is no longer invisible.
                logger.error(
                    "world_materialization.parse_failed genre=%s world=%s error=%s",
                    sd.genre_slug,
                    sd.world_slug,
                    exc,
                    exc_info=True,
                )
                span.add_event(
                    "history.parse_failed",
                    {
                        "event": "history.parse_failed",
                        "genre": sd.genre_slug,
                        "world": sd.world_slug,
                        "error": str(exc),
                        "exception_type": type(exc).__name__,
                        "exception_repr": repr(exc),
                    },
                )
                materialized = GameSnapshot(genre_slug=sd.genre_slug, world_slug=sd.world_slug)
                # Parse failed: cache an empty chapter list so the
                # arc-recompute tick is a graceful no-op rather than
                # propagating the parse error per turn.
                sd.cached_history_chapters = []
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

            # Magic Phase 4: instantiate MagicState on the canonical
            # snapshot for worlds that ship a magic.yaml pair (genre +
            # world). This is the production hook that pairs Phase 1's
            # loader with Phase 2's snapshot field — without it,
            # snapshot.magic_state stays None and the LedgerPanel never
            # surfaces bars even though the engine can apply workings
            # correctly. ``add_character`` instantiates per-character
            # bars (sanity / notice / vitality on Coyote Star) keyed
            # to the actor name the narrator emits in magic_working.
            init_magic_state_for_session(
                snapshot=sd.snapshot,
                genre_pack_source_dir=sd.genre_pack.source_dir,
                world_slug=sd.world_slug,
                character_id=character.core.name,
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

        # Story 45-2: chargen committed → seat transitions CHARGEN → PLAYING
        # so the turn barrier counts this peer. No-op if no room (solo
        # path with no MP room) or if the peer was already PLAYING (safe
        # under double-confirmation).
        if self._room is not None:
            self._room.transition_to_playing(player_id)

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
            party_status_msg = views.build_session_start_party_status(
                self, sd, character, player_id
            )
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
        """Handle PLAYER_ACTION — delegates to ``sidequest.handlers.player_action.HANDLER``."""
        from sidequest.handlers.player_action import HANDLER

        return await HANDLER.handle(self, msg)

    # ------------------------------------------------------------------
    # Narration execution — shared between player_action and opening turn
    # ------------------------------------------------------------------

    async def _execute_narration_turn(
        self,
        sd: _SessionData,
        action: str,
        turn_context: TurnContext,
        *,
        is_opening_turn: bool = False,
    ) -> list[object]:
        """Run one narration turn: orchestrator call, snapshot mutation,
        persistence, NARRATION + NARRATION_END message build.

        Shared by :meth:`_handle_player_action` and
        :meth:`_run_opening_turn_narration` (Story 2.3 Slice H). The
        caller owns TurnContext construction so each entrypoint can
        set per-turn fields (opening_directive on turn 0, pending
        trope beats on subsequent turns) without leaking responsibility.

        ``is_opening_turn`` (Story 45-5 / ADR-051): the chargen-confirmation
        narration sets the scene at round=1 / interaction=1 rather than
        completing a player-narrator exchange. Skipping
        ``record_interaction()`` for this turn keeps both counters at
        their fresh ``materialize_from_genre_pack`` defaults so post-
        chargen state is exactly ``(round=1, interaction=1)``. The
        narrative_log row this turn writes uses the pre-bump
        ``interaction=1``, so the 45-11 ``round_invariant`` (round ==
        MAX(narrative_log.round_number)) still holds. The first
        PLAYER_ACTION turn is the first real exchange and advances both
        counters in lockstep.
        """
        snapshot = sd.snapshot
        snapshot_before_hash = _hash_snapshot(snapshot)
        timings = PhaseTimings(action_received_monotonic=time.monotonic())
        turn_context.phase_timings = timings
        submitted = False
        # Story 45-20: capture trope-status baseline BEFORE any apply step
        # mutates statuses. The handshake fires post-record_interaction and
        # diffs this baseline against the live snapshot to detect any trope
        # whose status flipped to "resolved" — chapter promotion (today),
        # narrator extraction or engine tick (future). Capturing late
        # would mask the diff.
        trope_status_baseline: dict[str, str] = {
            t.id: t.status for t in snapshot.active_tropes
        }
        # Capture the watcher→OTLP synthetic-span counter at turn start so the
        # finally-block can log the per-turn delta. With this in the server
        # log a `grep turn.bridge_diagnostic /tmp/sidequest-server.log` reveals
        # whether the bridge minted any spans for this turn — Jaeger-empty
        # turns now have a hard, grep-able truth-value rather than a "did the
        # bridge fire?" guessing game (playtest 2026-04-30 #Jaeger-bridge).
        from sidequest.telemetry.watcher_hub import synthetic_spans_count  # noqa: PLC0415
        bridge_minted_at_start = synthetic_spans_count()
        try:
            with turn_span(
                turn_id=snapshot.turn_manager.interaction,
                player_id=sd.player_id,
                agent_name="narrator",
                genre=sd.genre_slug,
                world=sd.world_slug,
                action_len=len(action),
            ):
                # LocalDM is dormant on the live turn path as of 2026-04-28
                # (docs/superpowers/specs/2026-04-28-localdm-offline-only-design.md).
                # turn_context.dispatch_package stays None; build_narrator_prompt's
                # is-None guards skip redaction and the dispatch bank.

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
                with timings.phase("state_apply"):
                    dice_outcome = getattr(sd, "pending_roll_outcome", None)
                    dice_failed: bool | None = None
                    if dice_outcome is not None:
                        outcome_name = getattr(dice_outcome, "name", None) or str(dice_outcome)
                        dice_failed = outcome_name in ("Fail", "CritFail")
                    dice_actor: str | None = getattr(sd, "pending_roll_actor", None)
                    opposed_player_d20: int | None = getattr(
                        sd,
                        "pending_opposed_player_d20",
                        None,
                    )
                    opposed_player_beat_id: str | None = getattr(
                        sd,
                        "pending_opposed_player_beat_id",
                        None,
                    )
                    _apply_narration_result_to_snapshot(
                        snapshot,
                        result,
                        sd.player_name,
                        pack=sd.genre_pack,
                        dice_failed=dice_failed,
                        dice_actor=dice_actor,
                        opposed_player_d20=opposed_player_d20,
                        opposed_player_beat_id=opposed_player_beat_id,
                        opposed_player_actor=dice_actor,
                    )
                    # Consume the pending outcome — one turn per roll.
                    if dice_outcome is not None and hasattr(sd, "pending_roll_outcome"):
                        sd.pending_roll_outcome = None
                    if hasattr(sd, "pending_roll_actor"):
                        sd.pending_roll_actor = None
                    if hasattr(sd, "pending_opposed_player_d20"):
                        sd.pending_opposed_player_d20 = None
                    if hasattr(sd, "pending_opposed_player_beat_id"):
                        sd.pending_opposed_player_beat_id = None
                    # Story 45-5 / ADR-051: chargen is round 0; gameplay
                    # starts at round 1. The opening narration is the
                    # round-1 scene-set, not a player-narrator exchange,
                    # so it does not bump either counter. The first
                    # PLAYER_ACTION turn is the first real exchange.
                    if not is_opening_turn:
                        snapshot.turn_manager.record_interaction()

                    # Story 45-19: arc-recompute tick. Closes Felix's
                    # Playtest 3 bug where world_history froze at turn 30.
                    # The predicate is consulted with the post-bump
                    # interaction so cadence boundaries align with the
                    # interaction count the GM panel surfaces. Empty
                    # cached_history_chapters (pack with no history.yaml,
                    # or parse-failed chargen fallback) is a graceful
                    # no-op inside ``recompute_arc_history`` — the tick
                    # span still fires so the panel sees the empty case.
                    #
                    # Story 45-23: when the recompute reports newly-
                    # promoted chapters, seed each chapter's narrative
                    # log + lore strings into the durable narrative_log
                    # and the RAG-retrievable lore store. Closes Felix's
                    # writeback gap (71 turns, narrative_log + lore_store
                    # empty of arc-sourced content). Per-chapter
                    # ``arc_embedding_seed`` span carries the seeded
                    # counts so the GM panel can chart Lane B throughput.
                    if should_recompute_arc(snapshot.turn_manager.interaction):
                        added_chapters = recompute_arc_history(
                            snapshot, sd.cached_history_chapters
                        )
                        if added_chapters:
                            from sidequest.game.lore_seeding import (  # noqa: PLC0415
                                seed_lore_from_arc_promotion,
                            )
                            from sidequest.telemetry.spans import (  # noqa: PLC0415
                                SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED,
                                Span,
                            )
                            for chapter in added_chapters:
                                # One seed-call per promoted chapter so
                                # the OTEL span attributes can attribute
                                # the counts to the specific chapter id
                                # — the GM panel filters by chapter to
                                # diagnose which content got seeded.
                                seed_result = seed_lore_from_arc_promotion(
                                    snapshot,
                                    sd.store,
                                    sd.lore_store,
                                    [chapter],
                                )
                                with Span.open(
                                    SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED,
                                    {
                                        "chapter_id": getattr(chapter, "id", ""),
                                        "narrative_entries_appended": (
                                            seed_result.narrative_entries_appended
                                        ),
                                        "lore_fragments_minted": (
                                            seed_result.lore_fragments_minted
                                        ),
                                        "lore_fragments_skipped_duplicate": (
                                            seed_result.lore_fragments_skipped_duplicate
                                        ),
                                        "content_bytes_seeded": (
                                            seed_result.content_bytes_seeded
                                        ),
                                        "interaction": (
                                            snapshot.turn_manager.interaction
                                        ),
                                    },
                                ):
                                    pass

                    # Story 45-20: trope-resolution handshake. Diffs the
                    # baseline captured at the top of this method against
                    # the post-recompute snapshot to detect any trope that
                    # flipped to "resolved" this turn (chapter-promotion
                    # path today; engine/narrator-extraction future).
                    # Writes the durable record (quest_log entry +
                    # active_stakes marker) and emits the handshake span
                    # so the GM panel sees the path engaged. Idempotent
                    # re-detect (already-resolved last turn) emits a span
                    # with active_stakes_appended=False but does not
                    # rewrite — the lie-detector signal Sebastien needs.
                    _handshake_resolved_tropes(
                        snapshot,
                        trope_status_baseline,
                        player_name=sd.player_name,
                        source="chapter_promotion",
                    )

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

                with timings.phase("persistence"):
                    try:
                        # ADR-037 Python port: room owns the canonical snapshot, so a
                        # plain room.save() is sufficient — there is no per-session
                        # divergence to merge. Falls back to sd.store.save when the
                        # legacy non-slug path didn't bind a room.
                        if self._room is not None:
                            self._room.save()
                        else:
                            sd.store.save(snapshot)
                        # Story 45-22: log the player's turn before the narrator
                        # response so the narrative_log shows both sources.
                        # Felix's Playtest 3 had 71 entries all author='narrator'
                        # because the player append site was missing — Sebastien
                        # could not distinguish player input from narrator
                        # inference on the GM panel. Skipped on the opening
                        # turn (no real player input — chargen-confirmation
                        # seeds the action programmatically).
                        if not is_opening_turn:
                            acting_name = _resolve_acting_character_name(
                                sd, self._room,
                            )
                            player_entry = NarrativeEntry(
                                timestamp=0,
                                round=snapshot.turn_manager.interaction,
                                author="player",
                                content=action,
                                tags=[],
                                speaker=acting_name,
                            )
                            sd.store.append_narrative(player_entry)
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

                # Story 45-11 — turn_manager.round invariant lie-detector.
                # Felix's Playtest 3 ended round=65 / max(narrative_log)=72
                # with nothing watching the divergence. Emit on EVERY tick
                # (whether or not the invariant holds) so the GM panel can
                # tell "engaged + clean" apart from "not engaged at all".
                # Read MAX(round_number) from the durable narrative_log,
                # not whatever in-memory mirror the snapshot carries —
                # the SQL value is the ground truth Felix's save proved
                # the snapshot can drift from.
                try:
                    max_narrative_round = int(sd.store.max_narrative_round())
                except Exception as exc:  # noqa: BLE001 — telemetry must never crash a turn
                    logger.warning(
                        "round_invariant.max_lookup_failed error=%s",
                        exc,
                    )
                    max_narrative_round = 0
                with round_invariant_span(
                    round=snapshot.turn_manager.round,
                    interaction=snapshot.turn_manager.interaction,
                    max_narrative_round=max_narrative_round,
                ):
                    # Span attributes are set by the helper; the body is
                    # intentionally empty — this is a point-in-time emit,
                    # not a wrapping span around downstream work.
                    pass

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
                    # visibility_sidecar stays None on the live turn — the dispatch
                    # package that fed aggregate_visibility(...) is dormant. MP wiring
                    # will reintroduce a visibility classifier; until then, peers see
                    # the same canonical narration.
                    visibility_sidecar=None,
                )
                # MP-03 Task 3: route through EventLog + ProjectionFilter before send.
                with timings.phase("broadcast"):
                    narration_msg = self._emit_event("NARRATION", narration_payload)

                # Pingpong 2026-04-26 [S3-REGRESSION]: emit a SCRAPBOOK_ENTRY for
                # every narration turn so the UI gallery has metadata to merge with
                # the IMAGE that lands later from the daemon. Pure reuse — no new
                # LLM calls. Fields come from the orchestrator result and the
                # snapshot the narrator just stamped.
                with timings.phase("dispatch_post"):
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
                    if result.secret_routes:
                        for _envelope in build_secret_note_events(
                            result.secret_routes,
                            turn_id=f"{sd.genre_slug}:{sd.world_slug}:{sd.player_id}:{snapshot.turn_manager.interaction}",
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
                        # Story 45-3: lie-detector for the post-narration
                        # CONFRONTATION emit. The narrator just opened or
                        # advanced an encounter; the dial is about to move
                        # on screen. Sebastien (mechanical-first player)
                        # needs a span confirming the engine emitted
                        # post-mutation momentum here, not just that the
                        # narrator's prose happened to mention combat.
                        # Only fires on the live branch (active emit
                        # carrying real metric values) — the clear-payload
                        # branch broadcasts active=false with empty
                        # metrics, so there is no post-mutation momentum
                        # to audit.
                        if now_live and now_encounter is not None:
                            with encounter_momentum_broadcast_span(
                                encounter_type=now_encounter.encounter_type,
                                player_metric_after=now_encounter.player_metric.current,
                                opponent_metric_after=now_encounter.opponent_metric.current,
                                source="narration_apply",
                                beat_id=None,
                            ):
                                confrontation_msg = self._emit_event(
                                    "CONFRONTATION",
                                    confrontation_payload,
                                )
                        else:
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
                                    pid
                                    for pid in self._room.connected_player_ids()
                                    if pid != sd.player_id
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

                with timings.phase("broadcast"):
                    # MP merged-dispatch: shared-world frames need to reach
                    # every connected socket in the room. NARRATION rides the
                    # _emit_event/EventLog path (durable, replayed on
                    # reconnect). The four shared-world envelopes built below
                    # — NARRATION_END / CHAPTER_MARKER / PARTY_STATUS /
                    # AUDIO_CUE — are NOT durable: they're built once at
                    # turn-end and never persisted. Two pingpong cycles on
                    # 2026-04-30 caught both halves of the broadcast bug:
                    #   (1) commit 4b90250 — peers missed all four because
                    #       they were only appended to ``outbound`` (which
                    #       ships to the dispatcher socket alone). Fix added
                    #       a peer-broadcast helper.
                    #   (2) follow-on bug — the dispatcher's *own* socket
                    #       missed the four envelopes whenever the dispatcher
                    #       reconnected mid-narration (browser refresh during
                    #       the 30-60s Claude await). The peer-broadcast
                    #       excluded the dispatcher's pre-await socket_id;
                    #       outbound.append delivered to the now-cancelled
                    #       writer task on the old socket; the new socket
                    #       got nothing because the envelopes aren't in
                    #       EventLog to replay. Last-submitter froze every
                    #       turn (Reproduced 3× in pingpong).
                    #
                    # Fix: emit shared-world frames via a single broadcast to
                    # every CURRENT socket in the room (exclude_socket_id=
                    # None). Picks up reconnected sockets that registered
                    # before the broadcast fires; the original socket either
                    # detached (clean) or is still attached (gets the frame
                    # like everyone else). Replaces both outbound.append and
                    # the peer-only broadcast — single delivery path, no
                    # double-send risk.
                    #
                    # Legacy non-slug path (self._room is None — only legacy
                    # genre/world connect tests reach this) still falls back
                    # to outbound.append so test fixtures without a room
                    # registry continue to work.
                    _has_room = self._room is not None
                    # OTEL lie-detector: emit one watcher event per shared-
                    # world frame that records every recipient socket_id
                    # plus the resolved player_id. The GM panel can verify
                    # all 4 sockets received NARRATION_END after the merged
                    # dispatch — the only way to catch silent regressions
                    # of this exact bug going forward.
                    def _emit_shared_world_frame(msg: object, frame_kind: str) -> None:
                        if not _has_room:
                            outbound.append(msg)
                            return
                        room = self._room
                        assert room is not None  # noqa: S101 — narrowed by _has_room
                        room.broadcast(msg, exclude_socket_id=None)
                        # OTEL lie-detector: emit one watcher event per
                        # shared-world frame keyed by recipient player_ids
                        # so the GM panel can verify the dispatcher AND every
                        # peer received the frame after the merged dispatch
                        # — the only way to catch silent regressions of the
                        # last-submitter-stuck bug going forward. Wrapped in
                        # try/except: the broadcast above is the load-bearing
                        # call; OTEL must never crash a turn (and in tests a
                        # stub Room may not expose `connected_player_ids`).
                        try:
                            recipients_method = getattr(room, "connected_player_ids", None)
                            recipient_player_ids = (
                                recipients_method() if callable(recipients_method) else []
                            )
                            slug_attr = getattr(room, "slug", "")
                            _watcher_publish(
                                "shared_world_frame_broadcast",
                                {
                                    "frame_kind": frame_kind,
                                    "slug": slug_attr,
                                    "recipient_count": len(recipient_player_ids),
                                    "recipient_player_ids": recipient_player_ids,
                                    "dispatcher_player_id": sd.player_id,
                                },
                                component="multiplayer",
                            )
                        except Exception as exc:  # noqa: BLE001 — telemetry must never crash a turn
                            logger.warning(
                                "shared_world_frame.watcher_publish_failed kind=%s error=%s",
                                frame_kind,
                                exc,
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
                        chapter_marker_msg = ChapterMarkerMessage(
                            payload=ChapterMarkerPayload(
                                title=None,
                                location=_resolve_location_display(
                                    sd.genre_pack, sd.world_slug, snapshot.location
                                ),
                            ),
                            player_id=sd.player_id,
                        )
                        _emit_shared_world_frame(chapter_marker_msg, "CHAPTER_MARKER")
                    # Story 45-1 — sealed-letter shared-world handshake.
                    # Build the canonical delta from the post-resolution
                    # snapshot and ride it on NARRATION_END so peers see
                    # ground-truth location/encounter/party formation
                    # (playtest 3 fix: stops narrator fabricating
                    # "collapsed corridor" between Orin and Blutka).
                    handshake_delta = build_shared_world_delta(
                        snapshot,
                        room=self._room,
                    )
                    # Magic Phase 4: ride the post-resolution magic_state on the
                    # NARRATION_END handshake. Sent every turn (not gated on the
                    # internal StateDelta.magic flag) — the UI is stateless on
                    # this payload and an unchanged dict is cheap; gating would
                    # silently desync the ledger after a reconnect.
                    magic_state_dict = (
                        snapshot.magic_state.model_dump(mode="json")
                        if snapshot.magic_state is not None
                        else None
                    )
                    narration_end_msg = NarrationEndMessage(
                        type="NARRATION_END",  # type: ignore[arg-type]
                        payload=NarrationEndPayload(
                            state_delta=_shared_world_delta_to_state_delta(
                                handshake_delta,
                                magic_state=magic_state_dict,
                            ),
                        ),
                        player_id=sd.player_id,
                    )
                    _emit_shared_world_frame(narration_end_msg, "NARRATION_END")

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
                    # header / CharacterSheet. Previously PARTY_STATUS
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
                            self_char = (
                                views.resolve_self_character(self, sd) or snapshot.characters[0]
                            )
                            party_status = views.build_session_start_party_status(
                                self, sd, self_char, sd.player_id
                            )
                            # MP merged-dispatch: every connected socket needs the
                            # post-narration party refresh (location/HP/inventory).
                            # The dispatcher-built payload is safe to broadcast as-is —
                            # each peer's UI resolves "(YOU)" via the seat_map-tagged
                            # player_id of the member whose ``name`` matches its
                            # connectedPlayerName, not via the dispatcher's player_id
                            # field. Pre-fix-2 (peer-only broadcast), peers got it
                            # but the dispatcher's reconnected socket missed it
                            # (pingpong 2026-04-30 follow-on); single broadcast
                            # path fixes both halves.
                            _emit_shared_world_frame(party_status, "PARTY_STATUS")
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
                # dance — the DJ is a local filesystem lookup. MP: same cue plays
                # for every player at the table — broadcast to peers so the music
                # bed transitions in lock-step with the shared narration.
                audio_cue = self._maybe_dispatch_audio(sd, result)
                if audio_cue is not None:
                    _emit_shared_world_frame(audio_cue, "AUDIO_CUE")

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
                                    patch_type="quest",
                                    fields_changed=list(result.quest_updates),
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

                        timings.mark_done()
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
                            phase_durations_ms=timings.to_dict(),
                            phase_call_counts=timings.phase_call_counts,
                            total_duration_ms=timings.total_ms,
                            footnotes_count=len(result.footnotes or []),
                        )
                        await self._validator.submit(record)
                        submitted = True
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("turn_record.assemble_failed: %s", exc)

                # Per-turn `game_state_snapshot` for the dashboard State tab
                # (playtest 2026-04-30 #1C). Pre-fix this event was published
                # only at session connect / chargen confirmation — after the
                # initial fire the State tab read "Waiting for
                # GameStateSnapshot event..." forever. Per ADR-031 the watcher
                # is supposed to tick every turn so the GM panel can verify
                # state advancement; this publish closes that gap. Wrapped in
                # try/except so a serialization issue cannot crash the hot
                # turn path.
                try:
                    _watcher_publish(
                        "game_state_snapshot",
                        {
                            "reason": "turn",
                            "genre_slug": sd.genre_slug,
                            "world_slug": sd.world_slug,
                            "player_name": sd.player_name,
                            "player_id": sd.player_id,
                            "turn_number": snapshot.turn_manager.interaction,
                            # Full snapshot dump so the dashboard's State
                            # panel can render characters / NPCs / inventory
                            # / known facts / regions etc. Pre-fix the
                            # event payload was a thin summary
                            # (counts only) and the State panel could
                            # not draw any of its rich UI even when the
                            # event did fire at connect.
                            "snapshot": snapshot.model_dump(mode="json"),
                            # Back-compat summary fields the connect-time
                            # publishes have always exposed.
                            "current_location": snapshot.location or "",
                            "discovered_regions": list(snapshot.discovered_regions),
                            "npc_registry_count": len(snapshot.npc_registry),
                            "quest_log_count": len(snapshot.quest_log),
                            "lore_established_count": len(snapshot.lore_established),
                            "character_count": len(snapshot.characters),
                        },
                        component="game",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "game_state_snapshot.publish_failed turn=%d error=%s",
                        snapshot.turn_manager.interaction,
                        exc,
                    )

                return outbound
        finally:
            # Capture timings even when the turn raised — phase data is the
            # diagnostic signal we most need on failure paths.
            import contextlib  # noqa: PLC0415 — local import keeps module import lean

            with contextlib.suppress(Exception):  # finally must never re-raise
                timings.mark_done()

            # Per-turn watcher→OTLP bridge diagnostic + flush. Two birds:
            # (1) prove the bridge fired during this turn — a non-zero
            # ``minted`` value is hard evidence that publish_event saw the
            # ``SIDEQUEST_WATCHER_AS_SPANS`` flag and minted synthetic spans,
            # closing the "is the bridge live during gameplay?" question that
            # the resume-only Jaeger output kept open. (2) force a tracer
            # flush so the BatchSpanProcessor (default 2 s schedule) doesn't
            # hide turn-level spans from a live Jaeger viewer for the next
            # batch window. Both actions are wrapped in suppress() because
            # diagnostics must NEVER fail a turn.
            with contextlib.suppress(Exception):
                minted = synthetic_spans_count() - bridge_minted_at_start
                logger.info(
                    "turn.bridge_diagnostic minted=%d turn=%d player=%s "
                    "genre=%s world=%s",
                    minted,
                    snapshot.turn_manager.interaction,
                    sd.player_id,
                    sd.genre_slug,
                    sd.world_slug,
                )
            with contextlib.suppress(Exception):
                provider = trace.get_tracer_provider()
                # ``force_flush`` is on the SDK ``TracerProvider``; the proxy
                # provider used in tests / pre-init paths doesn't have it. A
                # hasattr check avoids importing the SDK class here just to
                # isinstance-check it.
                flush = getattr(provider, "force_flush", None)
                if callable(flush):
                    flush(timeout_millis=200)

            if not submitted and self._validator is not None:
                try:
                    degraded_record = TurnRecord(
                        turn_id=snapshot.turn_manager.interaction,
                        timestamp=datetime.now(UTC),
                        player_id=sd.player_id,
                        player_input=action,
                        classified_intent="unknown",
                        agent_name="narrator",
                        narration="",
                        patches_applied=[],
                        snapshot_before_hash=snapshot_before_hash,
                        snapshot_after=snapshot,
                        delta=None,
                        beats_fired=[],
                        extraction_tier=0,
                        token_count_in=0,
                        token_count_out=0,
                        agent_duration_ms=0,
                        is_degraded=True,
                        phase_durations_ms=timings.to_dict(),
                        phase_call_counts=timings.phase_call_counts,
                        total_duration_ms=timings.total_ms,
                    )
                    await self._validator.submit(degraded_record)
                except Exception:  # noqa: BLE001
                    logger.exception("turn_record.degraded_submit_failed")

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
        # [S2-BUG] coyote_star regression). The connect-time guard in
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

        # Playtest 2026-04-29 BUG-LOW: when MP joiner-orientation fires (the
        # suppression branch above just zeroed the seed), the previous
        # fallback "I look around and take in my surroundings." gave the
        # narrator a generic, unattributed action — and the resulting
        # narration treated the host PC as if THEY had performed it
        # ("Laverne is in the pilot's couch, hands flat on her thighs..."),
        # because the narrator had no anchor for whose POV the orientation
        # belonged to. Naming the joining PC explicitly fixes the POV
        # attribution; the SOUL.md Agency strengthening (sibling fix in
        # this playtest cycle) keeps the narrator from inventing dialogue
        # for either PC. We resolve the joining PC's character name from
        # the snapshot — it was just appended in the second-commit branch,
        # so the joiner is the LAST entry in ``snapshot.characters``.
        joiner_orientation = sd.opening_seed is None and len(sd.snapshot.characters) > 1
        if joiner_orientation:
            joiner_char_name = (
                sd.snapshot.characters[-1].core.name
                if sd.snapshot.characters
                else (sd.player_name or "the new arrival")
            )
            action = (
                f"{joiner_char_name} steps into the scene and orients to "
                "the surroundings — describe their arrival from their "
                "point of view in a brief grounding paragraph. Do not "
                "generate dialogue, decisions, or new actions for any "
                "other PC already present."
            )
            source_tier = "mp_joiner_orientation"
        else:
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

        narrator_messages = await self._execute_narration_turn(
            sd, action, turn_context, is_opening_turn=True,
        )
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
            # Catalog-injected compose wiring (slice 1): the daemon scopes
            # CharacterCatalog / PlaceCatalog / StyleCatalog by (genre, world).
            # Without this field the daemon's compose conditional is dead and
            # every render falls through to the prose-subject prompt path.
            #
            # Bug #2a (playtest 2026-04-26) reinforced the same constraint:
            # the daemon's PromptComposer gate at
            # sidequest-daemon/sidequest_daemon/media/daemon.py:453 short-
            # circuits when ``params["world"]`` is absent, falling back to a
            # raw subject+mood+tags prompt with no genre/world style. That
            # silent fallback is why grimvault renders looked generic.
            # Sending ``world`` engages the explicit-recipes pipeline so the
            # world-scoped ``visual_style.yaml::positive_suffix`` actually
            # lands in the ART_SENSIBILITY.WORLD slot.
            "world": sd.world_slug,
        }
        # Portrait initials overlay (story 37-30 AC-4): the daemon's
        # portrait composer needs the character's display name to draw
        # the initials card. Other tiers ignore the field.
        if tier == "portrait":
            params["subject_name"] = sd.player_name
            # Catalog-injected compose, slice 2: emit a structured `pc:<slug>`
            # ref so the daemon's PromptComposer routes the portrait through
            # the catalog instead of falling through to the prose-subject
            # path. When the snapshot has a Character to project, we ship a
            # descriptor blob alongside; the daemon's `_get_composer` calls
            # `CharacterCatalog.add_pc` from it.
            pc_slug = _slugify_player_name(sd.player_name)
            params["characters"] = [f"pc:{pc_slug}"]
            descriptor = _build_pc_descriptor(sd, pc_slug)
            if descriptor is not None:
                params["pc_descriptor"] = descriptor
        elif tier == "scene_illustration":
            pc_slug = _slugify_player_name(sd.player_name)
            # Match daemon's `build_cue_from_params` read key. The portrait
            # branch above sets `characters`; this branch was previously
            # setting `participants`, which the daemon never reads — so the
            # PC ref never reached the composer's casting layer. The on-
            # the-wire field is `characters` for both tiers; the daemon
            # routes to portrait/illustration recipes by tier.
            params["characters"] = [f"pc:{pc_slug}"]
            # `sd.snapshot.location` is free-form narrator prose
            # (e.g. "Engine Bay", "Corridor Deck Three"), not a
            # `where:<slug>` ref. The daemon's PromptComposer rejects
            # non-`where:` location refs at PlaceCatalog.get(). Until the
            # server tracks slug-aware locations, send empty so
            # _resolve_location takes its by-design "transient location"
            # path and the action prose (subject) carries the setting.
            params["location"] = ""
            descriptor = _build_pc_descriptor(sd, pc_slug)
            if descriptor is not None:
                params["pc_descriptor"] = descriptor

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
                # Bug #2a lie-detector: surface the genre/world routing the
                # daemon will see. If ``world`` is empty here, the daemon's
                # PromptComposer gate will short-circuit and the render will
                # silently fall back to a styleless prompt.
                "genre": sd.genre_slug,
                "world": sd.world_slug,
            },
            component="render",
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
        """Pre-turn lore RAG retrieval. Delegates to ``lore_embed.retrieve_for_turn``.

        Phase 3 of session_handler decomposition (see
        docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).
        """
        from sidequest.server.dispatch import lore_embed

        return await lore_embed.retrieve_for_turn(self, sd, action)

    def _dispatch_embed_worker(self, sd: _SessionData) -> None:
        """Post-turn embed worker dispatch. Delegates to ``lore_embed.dispatch_worker``.

        Phase 3 of session_handler decomposition (see
        docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).
        """
        from sidequest.server.dispatch import lore_embed

        lore_embed.dispatch_worker(self, sd)

    async def _run_embed_worker(
        self, sd: _SessionData, pending_count: int, turn_number: int
    ) -> None:
        """Background embed worker. Delegates to ``lore_embed.run_worker``.

        Phase 3 of session_handler decomposition (see
        docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).
        """
        from sidequest.server.dispatch import lore_embed

        await lore_embed.run_worker(self, sd, pending_count, turn_number)

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

        # Story 37-30 — resolve the live room at completion time, not at
        # dispatch. When the room is known, look up the *current*
        # SessionRoom via the RoomRegistry so reconnects mid-render still
        # land on live sockets.
        #
        # Bug #2b (playtest 2026-04-26): IMAGE used to land on a single
        # per-player queue (the originating actor's socket). Shared-world
        # scene imagery (POI/encounter/location/illustration) should be
        # shared across all connected players in the room — so peers see
        # the same image event. Switch to ``room.broadcast(msg)`` so
        # every attached outbound queue receives the IMAGE. The legacy
        # single-queue path remains for non-room-context tests and the
        # deprecated genre/world connect path.
        recipients_count = 0
        broadcast_used = False
        if room_slug is not None:
            registry = self._room_registry
            room = registry.get(room_slug) if registry is not None else None
            if room is None:
                # No live room — surface as session_not_found so the GM
                # panel sees the drop instead of it being silent.
                logger.warning(
                    "render.session_not_found render_id=%s room=%s player=%s reason=room_missing",
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
                        "reason": "room_missing",
                    },
                    component="render",
                    severity="warning",
                )
                return
            # Broadcast to every connected socket in the room. We do NOT
            # exclude the originating player — they need the IMAGE too,
            # mirroring the SCRAPBOOK_ENTRY/_emit_event fan-out pattern.
            try:
                room.broadcast(msg, exclude_socket_id=None)
            except Exception as exc:  # noqa: BLE001 — broadcast failure must surface
                logger.warning(
                    "render.broadcast_failed render_id=%s error=%s",
                    render_id,
                    exc,
                )
                _watcher_publish(
                    "state_transition",
                    {
                        "field": "render",
                        "op": "broadcast_failed",
                        "render_id": render_id,
                        "room_slug": room_slug,
                        "player_id": player_id,
                        "url": served_url,
                        "error": type(exc).__name__,
                    },
                    component="render",
                    severity="error",
                )
                return
            broadcast_used = True
            # Approximate recipient count for OTEL (lie-detector) — this
            # is the set of sockets that had a live outbound queue at
            # broadcast time.
            recipients_count = len(room.connected_player_ids())
            if recipients_count == 0:
                logger.warning(
                    "render.broadcast_no_recipients render_id=%s room=%s",
                    render_id,
                    room_slug,
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
                        "reason": "no_connected_players",
                    },
                    component="render",
                    severity="warning",
                )
                return
        else:
            # Legacy / test path: no room context, fall back to the
            # single per-connection queue captured at dispatch.
            target_queue = legacy_queue
            if target_queue is None:
                logger.warning(
                    "render.session_not_found render_id=%s reason=no_queue",
                    render_id,
                )
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
            recipients_count = 1

        logger.info(
            "render.completed render_id=%s url=%s elapsed_ms=%d recipients=%d broadcast=%s",
            render_id,
            served_url,
            elapsed,
            recipients_count,
            broadcast_used,
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
                # Bug #2b lie-detector: surface whether the IMAGE was
                # delivered as a shared-world broadcast or via the
                # legacy single-queue path, plus the recipient count.
                # Without this, "image only reached one player" was an
                # invisible regression (playtest 2026-04-26).
                "broadcast": broadcast_used,
                "recipients": recipients_count,
            },
            component="render",
        )
