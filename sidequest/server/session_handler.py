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
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from opentelemetry import trace

from sidequest.agents.claude_client import ClaudeClient, ClaudeLike
from sidequest.agents.orchestrator import Orchestrator, TurnContext
from sidequest.game.archetype_apply import apply_archetype_resolved
from sidequest.game.builder import (
    BuilderError,
    CharacterBuilder,
)
from sidequest.game.character import Character
from sidequest.game.lore_seeding import seed_lore_from_char_creation
from sidequest.game.lore_store import LoreStore
from sidequest.game.persistence import SqliteStore, db_path_for_session
from sidequest.game.room_movement import (
    RoomGraphInitError,
    init_room_graph_location,
)
from sidequest.game.session import GameSnapshot, NarrativeEntry
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
from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
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
from sidequest.server.dispatch.chargen_loadout import apply_starting_loadout
from sidequest.server.dispatch.chargen_summary import render_confirmation_summary
from sidequest.server.dispatch.opening_hook import resolve_opening
from sidequest.server.dispatch.scenario_bind import bind_scenario
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
        elif msg_type == "CHARACTER_CREATION":
            return await self._handle_character_creation(msg)
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
            return self._chargen_confirmation(builder, sd, player_id, span)
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
    def _chargen_confirmation(
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
        return [CharacterCreationMessage(payload=payload, player_id=player_id)]

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
