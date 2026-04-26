"""In-memory per-slug room: who is connected, who is seated, solo-slot enforcement.

One SessionRoom exists per game slug. Lives for the life of the process; content
is derivable from the save so loss on restart is acceptable (players reconnect
and re-seat).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable

from sidequest.game.persistence import GameMode, SqliteStore
from sidequest.game.session import GameSnapshot

# Imported lazily inside the typing block to avoid an import cycle —
# Orchestrator's module imports from sidequest.game (transitively),
# and sidequest.server.session_room is imported very early.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sidequest.agents.orchestrator import Orchestrator


class SoloSlotConflict(Exception):
    """Raised when a second player tries to connect to a solo game."""


@dataclass
class _Seat:
    player_id: str
    character_slot: str | None = None


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
    # Canonical narrator orchestrator (ADR-067 — single persistent narrator
    # session per slug). Each WS session bound to this room uses the
    # same Orchestrator so that two players acting on the same slug
    # share one Claude --resume session, one ``_narrator_session_id``,
    # and one consistent narration of the shared world. Without this,
    # each player constructs their own Orchestrator at connect time and
    # the system collapses into parallel solo games — see playtest
    # 2026-04-26 "MP — players run as parallel solo games".
    _orchestrator: "Orchestrator | None" = field(default=None, repr=False)
    # ADR-036 Cinematic mode — round-level action buffer keyed by player_id.
    # Drained by the elected dispatcher when TurnManager.submit_input flips
    # the barrier from InputCollection to IntentRouting. See spec
    # docs/superpowers/specs/2026-04-26-mp-cinematic-mode-wiring-design.md.
    _pending_actions: dict[str, PendingAction] = field(default_factory=dict)

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
    ) -> None:
        """Bind canonical snapshot + store to the room. Idempotent.

        First slug-connect on the room calls this with the loaded (or
        freshly constructed) snapshot. Subsequent connects observe the
        existing binding via the ``snapshot`` / ``store`` properties and
        do not call ``bind_world`` themselves; this idempotency is
        defense for any path that does retry the bind.
        """
        with self._lock:
            if self._snapshot is not None:
                return
            self._snapshot = snapshot
            self._store = store

    @property
    def snapshot(self) -> GameSnapshot | None:
        """Canonical snapshot for the slug, or None before first bind."""
        return self._snapshot

    @property
    def store(self) -> SqliteStore | None:
        """Canonical SqliteStore for the slug, or None before first bind."""
        return self._store

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
        factory: Callable[[], "Orchestrator"],
    ) -> "Orchestrator":
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
    def orchestrator(self) -> "Orchestrator | None":
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

    def disconnect(self, *, socket_id: str) -> str | None:
        with self._lock:
            player_id = self._sockets.pop(socket_id, None)
            if player_id is None:
                return None
            # Only remove from _connected if this socket is still the active one for that player.
            if self._connected.get(player_id) == socket_id:
                self._connected.pop(player_id, None)
            return player_id

    def seat(self, player_id: str, *, character_slot: str | None) -> None:
        with self._lock:
            self._seated[player_id] = _Seat(player_id=player_id, character_slot=character_slot)

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

    def record_pending_action(
        self, player_id: str, character_name: str, action: str,
    ) -> None:
        """Buffer one player's action for the current round (ADR-036).

        Last-write-wins on duplicate submissions for the same player_id.
        """
        with self._lock:
            self._pending_actions[player_id] = PendingAction(
                character_name=character_name, action=action,
            )

    def drain_pending_actions(self) -> list[tuple[str, PendingAction]]:
        """Return buffered actions in submission order and clear the buffer.

        Returns ``[(player_id, PendingAction), ...]``. Order matters because
        the combined-prose builder labels speakers in this order.
        """
        with self._lock:
            drained = list(self._pending_actions.items())
            self._pending_actions.clear()
        return drained

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
        """Game is paused if any seated player is not currently connected."""
        return len(self.absent_seated_player_ids()) > 0

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

    def broadcast(self, msg: Any, *, exclude_socket_id: str | None = None) -> None:
        """Put msg into every registered outbound queue except exclude_socket_id.

        Thread-safe: snapshot the target list under the lock, then put_nowait
        outside (put_nowait never blocks and the queues are asyncio-safe).
        """
        with self._lock:
            targets = [
                (sid, q)
                for sid, q in self._outbound_queues.items()
                if sid != exclude_socket_id
            ]
        for _sid, q in targets:
            q.put_nowait(msg)


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
