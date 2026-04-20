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

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable

from sidequest.agents.claude_client import ClaudeLike, ClaudeClient
from sidequest.agents.orchestrator import Orchestrator, TurnContext
from sidequest.game.persistence import SqliteStore, db_path_for_session
from sidequest.game.session import GameSnapshot, NarrativeEntry
from sidequest.genre.loader import GenreLoader, DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.genre.models.pack import GenrePack
from sidequest.protocol import GameMessage, sanitize_player_text
from sidequest.protocol.messages import (
    ErrorMessage,
    ErrorPayload,
    NarrationEndMessage,
    NarrationEndPayload,
    NarrationMessage,
    NarrationPayload,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.protocol.types import NonBlankString
from sidequest.telemetry.spans import (
    SPAN_ORCHESTRATOR_PROCESS_ACTION,  # noqa: F401 — re-exported for OTEL catalog consumers
    orchestrator_process_action_span,
)

logger = logging.getLogger(__name__)


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

    # ------------------------------------------------------------------
    # Public entrypoints
    # ------------------------------------------------------------------

    async def handle_message(self, msg: GameMessage) -> list[object]:
        """Dispatch an inbound message; return list of outbound protocol message objects."""
        msg_type: str = msg.type  # type: ignore[attr-defined]

        if msg_type == "SESSION_EVENT":
            return await self._handle_session_event(msg)
        elif msg_type == "PLAYER_ACTION":
            return await self._handle_player_action(msg)
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

        self._session_data = _SessionData(
            genre_slug=genre_slug,
            world_slug=world_slug,
            player_name=player_name,
            player_id=player_id,
            snapshot=snapshot,
            store=store,
            genre_pack=genre_pack,
            orchestrator=orchestrator,
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

        return [connected_msg]

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

        # Transition to Playing on first action (handles chargen via narration)
        if self._state == _State.Creating:
            self._state = _State.Playing

        sd = self._session_data
        snapshot = sd.snapshot

        # Build state summary for narrator context
        state_summary = snapshot.model_dump_json(indent=2)

        # Resolve character name
        char_name: str
        if snapshot.characters:
            char_name = snapshot.characters[0].core.name
        else:
            char_name = sd.player_name

        # Build TurnContext
        turn_context = TurnContext(
            in_combat=False,
            in_chase=False,
            in_encounter=False,
            state_summary=state_summary,
            narrator_verbosity="standard",
            narrator_vocabulary="literary",
            genre=sd.genre_slug,
            genre_prompts=sd.genre_pack.prompts,
            character_name=char_name,
            current_location=snapshot.location or "Unknown",
            available_sfx=_sfx_ids_from_genre(sd.genre_pack),
            npc_registry=list(snapshot.npc_registry),
            npcs=list(snapshot.npcs),
        )

        # Run narration turn
        with orchestrator_process_action_span(action_len=len(action)):
            result = await sd.orchestrator.run_narration_turn(action, turn_context)

        logger.info(
            "session.narration_complete genre=%s world=%s degraded=%s duration_ms=%s",
            sd.genre_slug,
            sd.world_slug,
            result.is_degraded,
            result.agent_duration_ms,
        )

        # Apply state delta from game_patch extraction
        _apply_narration_result_to_snapshot(snapshot, result, sd.player_name)

        # Increment turn counter
        snapshot.turn_manager.record_interaction()

        # Persist after turn
        try:
            sd.store.save(snapshot)
            # Append narrative entry
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

        # Build outbound messages: NARRATION + NARRATION_END
        narration_text = result.narration or "(The world holds its breath...)"
        try:
            narration_nbs = NonBlankString(narration_text)
        except Exception:
            narration_nbs = NonBlankString("The world holds its breath...")

        narration_msg = NarrationMessage(
            type="NARRATION",  # type: ignore[arg-type]
            payload=NarrationPayload(
                text=narration_nbs,
                state_delta=None,
                footnotes=[],
            ),
            player_id=sd.player_id,
        )

        narration_end_msg = NarrationEndMessage(
            type="NARRATION_END",  # type: ignore[arg-type]
            payload=NarrationEndPayload(state_delta=None),
            player_id=sd.player_id,
        )

        return [narration_msg, narration_end_msg]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_msg(message: str, reconnect_required: bool = False) -> ErrorMessage:
    return ErrorMessage(
        type="ERROR",  # type: ignore[arg-type]
        payload=ErrorPayload(
            message=NonBlankString(message),
            reconnect_required=reconnect_required,
        ),
        player_id="",
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
    result: "object",
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

    # Quest updates
    if result.quest_updates:
        for quest_id, status in result.quest_updates.items():
            snapshot.quest_log[quest_id] = status
        logger.info(
            "state.quest_update count=%d player=%s",
            len(result.quest_updates),
            player_name,
        )

    # Lore established
    if result.lore_established:
        for lore in result.lore_established:
            if lore not in snapshot.lore_established:
                snapshot.lore_established.append(lore)

    # NPC registry — upsert from npcs_present
    from sidequest.game.session import NpcRegistryEntry
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
            logger.info("state.npc_registry_add name=%r turn=%d", npc_mention.name, turn_num)
        else:
            existing.last_seen_turn = turn_num
            existing.last_seen_location = snapshot.location or None
            if npc_mention.role:
                existing.role = npc_mention.role
            if npc_mention.pronouns:
                existing.pronouns = npc_mention.pronouns
            if npc_mention.appearance:
                existing.appearance = npc_mention.appearance
