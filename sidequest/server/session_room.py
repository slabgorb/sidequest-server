"""In-memory per-slug room: who is connected, who is seated, solo-slot enforcement.

One SessionRoom exists per game slug. Lives for the life of the process; content
is derivable from the save so loss on restart is acceptable (players reconnect
and re-seat).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

import sidequest.telemetry.watcher_hub as _hub
from sidequest.game.persistence import GameMode, SqliteStore
from sidequest.game.session import GameSnapshot
from sidequest.orbital.loader import (
    OrbitalContent,
    OrbitalContentMissingError,
    load_orbital_content,
)
from sidequest.server.session import Session
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

_log = logging.getLogger(__name__)

# Imported lazily inside the typing block to avoid an import cycle —
# Orchestrator's module imports from sidequest.game (transitively),
# and sidequest.server.session_room is imported very early.
if TYPE_CHECKING:
    from sidequest.agents.orchestrator import Orchestrator


class SoloSlotConflict(Exception):
    """Raised when a second player tries to connect to a solo game."""


def _emit_action_reveal_cleared(
    room: SessionRoom,
    *,
    player_id: str,
    character_name: str,
    round_no: int,
    reason: str,
) -> None:
    """Broadcast ACTION_REVEAL cleared for one player.

    reason is OTEL-only — wire payload is identical regardless of cause.
    """
    from sidequest.protocol.messages import (
        ActionRevealMessage,
        ActionRevealPayload,
        ActionRevealStatus,
    )

    payload = ActionRevealPayload(
        player_id=player_id,
        character_name=character_name,
        status=ActionRevealStatus.CLEARED,
        action="",
        aside=False,
        seq=0,
        round=round_no,
    )
    room.broadcast(ActionRevealMessage(payload=payload), exclude_socket_id=None)
    _watcher_publish(
        "action_reveal.cleared",
        {
            "slug": room.slug,
            "player_id": player_id,
            "round": round_no,
            "reason": reason,
        },
        component="multiplayer",
    )


class LobbyState(StrEnum):
    """Lifecycle of a peer's lobby slot (Story 45-2).

    Story 45-2 fix: the structured-mode turn barrier must count only
    PLAYING peers, not every seated lobby connection. Modeling lobby
    presence as an explicit state machine (vs. implicit booleans) lets
    the barrier predicate, the pause-banner predicate, and the
    chargen-abandonment edge each ask a different question of the same
    record without rotting.

    Storage and observability:
      - CHARGEN / PLAYING / ABANDONED are stored on _Seat.
      - CONNECTED is emitted as `lobby.state_transition` from `connect()`
        but not stored on _Seat (no seat exists yet at connect time).
      - CLAIMING_SEAT is defined for completeness — it represents the
        brief edge between PLAYER_SEAT receipt and the `seat()` call —
        but no code path currently emits a transition with
        `to_state=CLAIMING_SEAT` because that edge is a single
        function-call (`_handle_player_seat` validates and immediately
        calls `room.seat()`). Exported for forward extensibility; future
        code may use it without an enum change.
    """

    CONNECTED = "connected"  # WS open, no PLAYER_SEAT yet (emitted, not stored)
    CLAIMING_SEAT = "claiming_seat"  # reserved — not currently emitted or stored
    CHARGEN = "chargen"  # seat claimed, character builder active
    PLAYING = "playing"  # chargen committed, character in world
    ABANDONED = "abandoned"  # disconnected during chargen — reclaimable


# Watcher event names (Story 45-2). String constants here so call sites
# stay grep-friendly and so the names are reviewable in one place.
EVENT_LOBBY_STATE_TRANSITION = "lobby.state_transition"
EVENT_LOBBY_SEAT_ABANDONED = "lobby.seat_abandoned"


@dataclass
class _Seat:
    player_id: str
    character_slot: str | None = None
    # Story 45-2: explicit lifecycle. Default is CHARGEN because seat()
    # represents a fresh seat-claim; transition_to_playing() flips it
    # once the character is committed (chargen confirmation, or a
    # returning player whose character is already in the snapshot).
    state: LobbyState = LobbyState.CHARGEN


@dataclass
class PendingAction:
    """A buffered player action awaiting the round barrier (ADR-036).

    Resolved at submit time so the elected dispatcher reads the labeled
    prose back without re-resolving foreign player_ids without their
    session data. See spec
    docs/superpowers/specs/2026-04-26-mp-cinematic-mode-wiring-design.md.
    """

    character_name: str
    action: str


@dataclass
class SessionRoom:
    slug: str
    mode: GameMode
    # player_id -> socket_id (only connected players)
    _connected: dict[str, str] = field(default_factory=dict)
    _sockets: dict[str, str] = field(default_factory=dict)  # socket_id -> player_id
    _seated: dict[str, _Seat] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock, repr=False)
    # socket_id -> asyncio.Queue for per-socket outbound message fan-out (MP-02 Task 4)
    _outbound_queues: dict[str, asyncio.Queue[Any]] = field(default_factory=dict)
    # Canonical world state (ADR-037 Python port). The room owns the
    # GameSnapshot and SqliteStore for its slug; every WS session bound
    # to the room reads/writes the same in-memory snapshot reference.
    _snapshot: GameSnapshot | None = field(default=None, repr=False)
    _store: SqliteStore | None = field(default=None, repr=False)
    _session: Session | None = field(default=None, init=False, repr=False)
    # Canonical narrator orchestrator (ADR-067 — single persistent narrator
    # session per slug). Each WS session bound to this room uses the
    # same Orchestrator so that two players acting on the same slug
    # share one Claude --resume session, one ``_narrator_session_id``,
    # and one consistent narration of the shared world. Without this,
    # each player constructs their own Orchestrator at connect time and
    # the system collapses into parallel solo games — see playtest
    # 2026-04-26 "MP — players run as parallel solo games".
    _orchestrator: Orchestrator | None = field(default=None, repr=False)
    # ADR-036 Cinematic mode — round-level action buffer keyed by player_id.
    # Drained by the elected dispatcher when TurnManager.submit_input flips
    # the barrier from InputCollection to IntentRouting. See spec
    # docs/superpowers/specs/2026-04-26-mp-cinematic-mode-wiring-design.md.
    _pending_actions: dict[str, PendingAction] = field(default_factory=dict)
    # Monotonic timestamp of the first submission into an empty buffer, set
    # so the dispatching player_action handler can record `mp_barrier_wait`
    # — the elapsed time from "first player submits" to "barrier fires".
    # Cleared on drain. None when no submissions are pending.
    _first_pending_at_monotonic: float | None = field(default=None, repr=False)
    # Election primitives for one-dispatch-per-round (ADR-036). The lock
    # serializes elected handlers; the counter is the CAS guard so a
    # second handler that wakes after the first commits the dispatch
    # short-circuits instead of re-running the narrator. Counter source
    # is TurnManager.interaction (monotonic per-exchange), not .round
    # (advances on narrative beats only).
    _dispatch_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _last_dispatched_round: int = 0

    # ------------------------------------------------------------------
    # Canonical world state (ADR-037 Python port). The room owns the
    # GameSnapshot and SqliteStore; every WS session bound to this slug
    # reads and writes the same in-memory snapshot reference.
    # ------------------------------------------------------------------

    def bind_world(
        self,
        *,
        snapshot: GameSnapshot,
        store: SqliteStore,
        world_dir: Path | None = None,
    ) -> None:
        """Bind canonical snapshot + store to the room. Idempotent.

        First slug-connect on the room calls this with the loaded (or
        freshly constructed) snapshot. Subsequent connects observe the
        existing binding via the ``snapshot`` / ``store`` properties and
        do not call ``bind_world`` themselves; this idempotency is
        defense for any path that does retry the bind.

        ``world_dir`` is the resolved path to the bound world. When
        provided, attempts to load the orbital tier (``orbits.yaml`` +
        optional ``chart.yaml``) and exposes it via
        ``room.session.orbital_content``. Worlds without an orbital
        tier (no ``orbits.yaml``) bind cleanly with
        ``orbital_content=None``; malformed orbital data fails loud.
        """
        orbital_content: OrbitalContent | None = None
        if world_dir is not None:
            try:
                orbital_content = load_orbital_content(world_dir)
            except OrbitalContentMissingError:
                # Orbital tier is optional — caverns_and_claudes,
                # victoria, etc. have no orbits.yaml. Bind without
                # orbital content; chart UI will not be available.
                orbital_content = None
                _log.debug(
                    "session.no_orbital_tier slug=%s world_dir=%s",
                    self.slug,
                    world_dir,
                )
        with self._lock:
            if self._snapshot is not None:
                return
            self._snapshot = snapshot
            self._store = store
            self._session = Session(snapshot, orbital_content=orbital_content)

    def rebind_world(
        self,
        *,
        snapshot: GameSnapshot,
        store: SqliteStore | None = None,
        world_dir: Path | None = None,
    ) -> None:
        """Replace the bound snapshot/session — used at delve→hub transitions.

        ``bind_world`` short-circuits when a snapshot is already bound
        (the idempotency that protects concurrent slug-connect from
        constructing two sessions). Sünden's delve-end path needs to
        SWAP the snapshot when the room transitions back from delve
        mode to hub mode: the prior delve-mode snapshot must be torn
        out and a fresh hub-mode snapshot installed in its place.
        ``rebind_world`` is the explicit unbind+bind helper for that
        flow.

        ``store`` is optional — when omitted, the existing
        ``self._store`` is preserved (delve-end keeps the same SQLite
        handle; only the in-memory snapshot+session swap). When
        provided, replaces the bound store. World-dir handling mirrors
        ``bind_world``.
        """
        orbital_content: OrbitalContent | None = None
        if world_dir is not None:
            try:
                orbital_content = load_orbital_content(world_dir)
            except OrbitalContentMissingError:
                orbital_content = None
                _log.debug(
                    "session.no_orbital_tier_on_rebind slug=%s world_dir=%s",
                    self.slug,
                    world_dir,
                )
        with self._lock:
            self._snapshot = snapshot
            if store is not None:
                self._store = store
            self._session = Session(snapshot, orbital_content=orbital_content)

    @property
    def snapshot(self) -> GameSnapshot | None:
        """Canonical snapshot for the slug, or None before first bind."""
        return self._snapshot

    @property
    def store(self) -> SqliteStore | None:
        """Canonical SqliteStore for the slug, or None before first bind."""
        return self._store

    @property
    def session(self) -> Session:
        """Per-slug Session aggregate. Raises if not yet bound to a world."""
        if self._session is None:
            raise RuntimeError("Session not yet bound; call bind_world(snapshot, store) first.")
        return self._session

    def save(self) -> None:
        """Persist the canonical snapshot through the canonical store.

        Acquires ``_lock`` so concurrent saves from disconnect / turn-end
        / chargen-commit on different sessions don't interleave. No-op
        when the room hasn't been bound — paths that haven't reached
        slug-connect must not crash on save.
        """
        with self._lock:
            if self._snapshot is None or self._store is None:
                return
            self._store.save(self._snapshot)

    # ------------------------------------------------------------------
    # Canonical narrator orchestrator (ADR-067)
    # ------------------------------------------------------------------

    def get_or_create_orchestrator(
        self,
        factory: Callable[[], Orchestrator],
    ) -> Orchestrator:
        """Return the room's orchestrator, creating it via ``factory`` on
        first call. Atomic under ``_lock``: a peer connecting on the
        same slug at the same instant cannot construct a second
        Orchestrator (which would mean a second narrator session and a
        second Claude --resume id, breaking ADR-067's single-session
        guarantee). The factory is invoked only on the first call —
        subsequent callers receive the existing instance and the
        factory is never called, avoiding wasted Claude-client setup.
        """
        import logging as _logging

        _log = _logging.getLogger("sidequest.server.session_room")
        with self._lock:
            if self._orchestrator is None:
                self._orchestrator = factory()
                _log.info(
                    "room.orchestrator_created slug=%s orch_id=%s",
                    self.slug,
                    id(self._orchestrator),
                )
            else:
                _log.info(
                    "room.orchestrator_reused slug=%s orch_id=%s",
                    self.slug,
                    id(self._orchestrator),
                )
            return self._orchestrator

    @property
    def orchestrator(self) -> Orchestrator | None:
        """Canonical orchestrator for the slug, or None before first bind."""
        return self._orchestrator

    def close_store(self) -> None:
        """Close the canonical store exactly once. Idempotent.

        Called by ``RoomRegistry`` (or last-disconnect cleanup) so the
        underlying SQLite handle is released. Safe to call when never
        bound.
        """
        with self._lock:
            if self._store is None:
                return
            try:
                self._store.close()
            finally:
                self._store = None

    def connect(self, player_id: str, *, socket_id: str) -> None:
        with self._lock:
            if self.mode == GameMode.SOLO:
                other_players = [p for p in self._connected if p != player_id]
                if other_players:
                    raise SoloSlotConflict(
                        f"solo game {self.slug} already occupied by {other_players[0]}"
                    )
            # If same player reconnects on a new socket, drop the old socket mapping.
            old_socket = self._connected.get(player_id)
            if old_socket and old_socket != socket_id:
                self._sockets.pop(old_socket, None)
            self._connected[player_id] = socket_id
            self._sockets[socket_id] = player_id

        # Story 45-2: emit state-transition (CONNECTED is implicit / not
        # stored, but the GM panel still wants to see the edge fire).
        _hub.publish_event(
            EVENT_LOBBY_STATE_TRANSITION,
            {
                "player_id": player_id,
                "from_state": "(new)",
                "to_state": LobbyState.CONNECTED.value,
                "reason": "ws_connect",
            },
            component="lobby",
        )

    def disconnect(self, *, socket_id: str) -> str | None:
        # Capture transition info under the lock so the seat-state read and
        # the mutation happen atomically; emit watcher events outside the
        # lock (publish_event may be patched in tests / has its own
        # synchronization).
        abandon_payload: dict[str, Any] | None = None
        with self._lock:
            player_id = self._sockets.pop(socket_id, None)
            if player_id is None:
                return None
            # Only remove from _connected if this socket is still the active one for that player.
            if self._connected.get(player_id) == socket_id:
                self._connected.pop(player_id, None)
            seat = self._seated.get(player_id)
            if seat is not None and seat.state == LobbyState.CHARGEN:
                # Story 45-2 fix dimension #4: disconnect during chargen
                # cancels the seat. ABANDONED slots are reclaimable —
                # they are NOT counted by the turn barrier.
                seat.state = LobbyState.ABANDONED
                abandon_payload = {
                    "player_id": player_id,
                    "character_slot": seat.character_slot,
                    "from_state": LobbyState.CHARGEN.value,
                }

        if abandon_payload is not None:
            _hub.publish_event(
                EVENT_LOBBY_STATE_TRANSITION,
                {
                    "player_id": player_id,
                    "from_state": LobbyState.CHARGEN.value,
                    "to_state": LobbyState.ABANDONED.value,
                    "reason": "chargen_disconnect",
                },
                component="lobby",
            )
            _hub.publish_event(
                EVENT_LOBBY_SEAT_ABANDONED,
                abandon_payload,
                component="lobby",
            )
        # ADR-036 Action Visibility Model: clear the departed player's
        # reveal row from peers so they don't see a frozen "composing"
        # with no sender.
        if player_id is not None:
            snapshot = self.snapshot
            round_no = snapshot.turn_manager.round if snapshot is not None else 0
            seat = self._seated.get(player_id)
            # _Seat has no character_name (it's pre-chargen); fall back to
            # player_id so NonBlankString requirement on ActionRevealPayload
            # is satisfied even when the character hasn't been named yet.
            character_name = player_id
            if seat is not None and getattr(seat, "character_name", None):
                character_name = seat.character_name
            _emit_action_reveal_cleared(
                self,
                player_id=player_id,
                character_name=character_name,
                round_no=round_no,
                reason="disconnect",
            )
        return player_id

    def seat(self, player_id: str, *, character_slot: str | None) -> None:
        # Track the prior state under the lock so the from_state on the
        # transition event is accurate even on the rare re-seat path.
        with self._lock:
            existing = self._seated.get(player_id)
            from_state = (
                existing.state.value if existing is not None else LobbyState.CONNECTED.value
            )
            # New seat starts in CHARGEN by default — transition_to_playing()
            # flips to PLAYING once the character is committed.
            self._seated[player_id] = _Seat(
                player_id=player_id,
                character_slot=character_slot,
            )

        _hub.publish_event(
            EVENT_LOBBY_STATE_TRANSITION,
            {
                "player_id": player_id,
                "from_state": from_state,
                "to_state": LobbyState.CHARGEN.value,
                "reason": "seat_claim",
            },
            component="lobby",
        )

    def transition_to_playing(self, player_id: str) -> None:
        """Transition a seat from CHARGEN to PLAYING (Story 45-2).

        Called from `_chargen_confirmation()` after `builder.build()`
        succeeds, and from `_handle_player_seat()` for returning players
        whose character is already in the snapshot. Idempotent: a
        no-op if the seat is already PLAYING (silent — no duplicate
        transition event). No-op if no seat exists (returning-player
        race where PLAYER_SEAT lands before connect-time bookkeeping).
        """
        with self._lock:
            seat = self._seated.get(player_id)
            if seat is None:
                return
            if seat.state == LobbyState.PLAYING:
                return
            from_state = seat.state.value
            seat.state = LobbyState.PLAYING

        _hub.publish_event(
            EVENT_LOBBY_STATE_TRANSITION,
            {
                "player_id": player_id,
                "from_state": from_state,
                "to_state": LobbyState.PLAYING.value,
                "reason": "chargen_complete",
            },
            component="lobby",
        )

    def unseat(self, player_id: str) -> None:
        with self._lock:
            self._seated.pop(player_id, None)

    def connected_player_ids(self) -> list[str]:
        with self._lock:
            return list(self._connected.keys())

    def seated_player_ids(self) -> list[str]:
        with self._lock:
            return list(self._seated.keys())

    def absent_seated_player_ids(self) -> list[str]:
        with self._lock:
            return [p for p in self._seated if p not in self._connected]

    def playing_player_ids(self) -> list[str]:
        """Story 45-2: only PLAYING peers count toward the turn barrier.

        Sibling to `seated_player_ids()` — `seated` returns every seat
        record (CHARGEN / PLAYING / ABANDONED), `playing` filters to
        peers whose character is committed and in the world. The turn
        barrier predicate at session_handler.py reads `playing_*` so
        that phantom chargen peers don't block solo players (the evropi
        2026-04-19 scenario).
        """
        with self._lock:
            return [pid for pid, seat in self._seated.items() if seat.state == LobbyState.PLAYING]

    def playing_player_count(self) -> int:
        """Number of PLAYING peers — input to the turn barrier (Story 45-2)."""
        return len(self.playing_player_ids())

    def non_abandoned_player_count(self) -> int:
        """Story 45-2: count of seats with `state != ABANDONED`.

        This is the `lobby_participant_count` source for the `barrier.wait`
        OTEL event (Sebastien's lie-detector reads `lobby > active` to see
        phantom-peer pressure). The raw `seated_player_count()` is not
        suitable because it counts ABANDONED slots — historical
        chargen-failure orphans that shouldn't inflate the lobby count.
        Sibling to `playing_player_count()`, which requires `state ==
        PLAYING`; a CHARGEN seat is counted here but not there.
        """
        with self._lock:
            return sum(1 for seat in self._seated.values() if seat.state != LobbyState.ABANDONED)

    def record_pending_action(
        self,
        player_id: str,
        character_name: str,
        action: str,
    ) -> None:
        """Buffer one player's action for the current round (ADR-036).

        Last-write-wins on duplicate submissions for the same player_id.
        Stamps `_first_pending_at_monotonic` on transition-from-empty so
        the dispatcher can later compute `mp_barrier_wait`.
        """
        with self._lock:
            if not self._pending_actions and self._first_pending_at_monotonic is None:
                self._first_pending_at_monotonic = time.monotonic()
            self._pending_actions[player_id] = PendingAction(
                character_name=character_name,
                action=action,
            )

    def first_pending_at_monotonic(self) -> float | None:
        """Read the timestamp stamped when the buffer transitioned from empty.

        Returns ``None`` when no submissions are pending. Callers should
        copy the value before draining — drain clears it.
        """
        with self._lock:
            return self._first_pending_at_monotonic

    def drain_pending_actions(self) -> list[tuple[str, PendingAction]]:
        """Return buffered actions in submission order and clear the buffer.

        Returns ``[(player_id, PendingAction), ...]``. Order matters because
        the combined-prose builder labels speakers in this order. Also
        clears the barrier-wait timestamp so the next round starts fresh.
        """
        with self._lock:
            drained = list(self._pending_actions.items())
            self._pending_actions.clear()
            self._first_pending_at_monotonic = None
        return drained

    @property
    def dispatch_lock(self) -> asyncio.Lock:
        """The per-room dispatch election lock (ADR-036)."""
        return self._dispatch_lock

    @property
    def last_dispatched_round(self) -> int:
        """Highest interaction counter for which a narrator dispatch has fired.

        Named ``round`` for ADR-036 nomenclature, but the counter source is
        ``TurnManager.interaction`` (which advances on every player-narrator
        exchange) rather than ``TurnManager.round`` (which advances only on
        meaningful narrative beats). This guarantees CAS uniqueness across
        sequential dispatches.
        """
        return self._last_dispatched_round

    @last_dispatched_round.setter
    def last_dispatched_round(self, value: int) -> None:
        self._last_dispatched_round = value

    def seated_player_count(self) -> int:
        """Number of seated players, regardless of connection state."""
        return len(self.seated_player_ids())

    def slot_to_player_id(self) -> dict[str, str]:
        """Return a snapshot of {character_slot: player_id} for seated players.

        Used by PARTY_STATUS construction in multiplayer to map peer
        characters (identified by their slot label, e.g. "Shirley") back
        to the player_id that claimed the seat. Seats with no slot label
        are skipped. Returns an empty dict in solo / pre-seat states.
        """
        with self._lock:
            return {
                seat.character_slot: pid
                for pid, seat in self._seated.items()
                if seat.character_slot is not None
            }

    def is_paused(self) -> bool:
        """Game is paused if any PLAYING peer is not currently connected.

        Story 45-2 (AC6 regression): chargen-abandoned peers do NOT pause
        the game — their slot is reclaimable, not held. Only PLAYING peers
        (committed character, in-world presence) hold the slot across a
        disconnect. CHARGEN peers either stay connected (no pause needed)
        or transition to ABANDONED on disconnect (see `disconnect()`).

        Iteration semantics: this predicate iterates over every seat in
        `_seated` regardless of state. Seats with `state != PLAYING` —
        whether CHARGEN-still-connected or ABANDONED — are silently
        excluded by the `state == PLAYING` filter. ABANDONED seats remain
        in `_seated` for forensic/GM-panel inspection, but they never
        contribute to pause.
        """
        with self._lock:
            return any(
                pid not in self._connected and seat.state == LobbyState.PLAYING
                for pid, seat in self._seated.items()
            )

    # ------------------------------------------------------------------
    # Outbound queue management (MP-02 Task 4)
    # ------------------------------------------------------------------

    def attach_outbound(self, socket_id: str, queue: asyncio.Queue[Any]) -> None:
        """Register a per-socket outbound queue for broadcast delivery."""
        with self._lock:
            self._outbound_queues[socket_id] = queue

    def detach_outbound(self, socket_id: str) -> None:
        """Deregister a per-socket outbound queue (called on disconnect)."""
        with self._lock:
            self._outbound_queues.pop(socket_id, None)

    def socket_for_player(self, player_id: str) -> str | None:
        """Return the socket_id for a connected player, or None if not connected."""
        with self._lock:
            return self._connected.get(player_id)

    def queue_for_socket(self, socket_id: str) -> asyncio.Queue[Any] | None:
        """Return the outbound queue for a socket, or None if not registered."""
        with self._lock:
            return self._outbound_queues.get(socket_id)

    def broadcast(
        self, msg: Any, *, exclude_socket_id: str | None = None
    ) -> list[tuple[str, str | None]]:
        """Put msg into every registered outbound queue except exclude_socket_id.

        Thread-safe: snapshot the target list under the lock, then put_nowait
        outside (put_nowait never blocks and the queues are asyncio-safe).

        Returns the list of ``(socket_id, player_id)`` pairs the message was
        actually queued onto. ``player_id`` is ``None`` for sockets that have
        an outbound queue registered but no current ``_sockets`` entry (a
        transient race during reconnect; should not happen in steady state).
        Callers can use the return value as the lie-detector ground truth —
        ``recipients = len(_connected)`` over-reports when ``_outbound_queues``
        and ``_connected`` diverge, and the mismatch silently strands peers.
        Pingpong 2026-04-30 "Scrapbook only on first-connected player" was
        caught precisely because the broadcast log claimed ``recipients=4``
        while only the host's queue actually received the IMAGE.
        """
        with self._lock:
            targets = [
                (sid, self._sockets.get(sid), q)
                for sid, q in self._outbound_queues.items()
                if sid != exclude_socket_id
            ]
        for _sid, _pid, q in targets:
            q.put_nowait(msg)
        return [(sid, pid) for sid, pid, _q in targets]


class RoomRegistry:
    def __init__(self) -> None:
        self._rooms: dict[str, SessionRoom] = {}
        self._lock = RLock()

    def get_or_create(self, slug: str, *, mode: GameMode) -> SessionRoom:
        with self._lock:
            existing = self._rooms.get(slug)
            if existing is not None:
                return existing
            room = SessionRoom(slug=slug, mode=mode)
            self._rooms[slug] = room
            return room

    def get(self, slug: str) -> SessionRoom | None:
        with self._lock:
            return self._rooms.get(slug)
