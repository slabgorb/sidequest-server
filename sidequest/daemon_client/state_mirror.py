"""Server-side mirror of the daemon's per-queue worker state.

Story 45-31: replaces the binary ``client.is_available()`` (socket-on-disk)
check at ``websocket_session_handler.py:3210`` with a continuous
liveness signal. The daemon publishes ``{"event":"heartbeat",
"queue":..., "state":..., "queue_depth":..., "ts_monotonic":...}``
events; ``DaemonClient.heartbeat_listener`` reads them and feeds this
mirror via :meth:`DaemonStateMirror.record_heartbeat`. The dispatcher
calls :meth:`DaemonStateMirror.is_unresponsive` to decide whether to
short-circuit the daemon round-trip and surface a
``render.unavailable`` event with a degradation marker on the
scrapbook row (the explicit Felix anti-13-minute-silence contract).

The module exposes :func:`get_mirror` — a process-wide singleton — so
the heartbeat reader and the dispatcher share state. Tests reset the
singleton in fixtures via :meth:`DaemonStateMirror.clear`.
"""

from __future__ import annotations

import builtins
import threading
import time
from enum import StrEnum

_BUILTINS_MIRROR_ATTR = "_sidequest_daemon_state_mirror_singleton"


class DaemonState(StrEnum):
    """Mirror states the dispatcher branches on.

    - ``READY``: daemon is alive and accepting work.
    - ``BUSY``: at least one render or embed is in flight.
    - ``PAUSED``: the GPU coordinator (ADR-046) gated the queue. The
      dispatcher treats this like UNRESPONSIVE for now (no work
      dispatched) but the watcher event distinguishes the cause.
    - ``UNRESPONSIVE``: no heartbeat seen for >2× interval. The
      dispatcher MUST skip the daemon round-trip and emit the
      ``render.unavailable`` fallback.
    """

    READY = "ready"
    BUSY = "busy"
    PAUSED = "paused"
    UNRESPONSIVE = "unresponsive"


_VALID_HEARTBEAT_STATES = frozenset({"ready", "busy", "paused", "cold"})


class DaemonStateMirror:
    """Thread-safe per-queue mirror of the daemon's heartbeat stream.

    A fresh mirror starts in :attr:`DaemonState.UNRESPONSIVE` for every
    queue — the daemon has not announced itself yet, and the safe
    default is "fail closed" until a heartbeat lands.
    """

    def __init__(self, *, heartbeat_interval_seconds: float = 30.0) -> None:
        self._heartbeat_interval = heartbeat_interval_seconds
        self._lock = threading.Lock()
        self._queue_states: dict[str, DaemonState] = {}
        self._queue_depths: dict[str, int] = {}
        self._last_heartbeat_ts: float | None = None
        # When set, ``is_unresponsive`` returns True regardless of the
        # wall clock. Used by the unavailable-fallback test to drive
        # the dispatcher into the degradation branch deterministically.
        self._force_unresponsive: bool = False

    # ------------------------------------------------------------------
    # Inputs (heartbeat reader → mirror)
    # ------------------------------------------------------------------

    def record_heartbeat(
        self,
        *,
        queue: str,
        state: str,
        queue_depth: int,
        ts_monotonic: float,
    ) -> None:
        """Record one heartbeat from the daemon.

        Per CLAUDE.md "No Silent Fallbacks": an unknown ``state`` MUST
        raise — silent acceptance of a typo'd state would let a daemon
        bug masquerade as health.
        """
        if state not in _VALID_HEARTBEAT_STATES:
            raise ValueError(
                f"unknown heartbeat state {state!r}; "
                f"expected one of {sorted(_VALID_HEARTBEAT_STATES)}"
            )
        # Map the heartbeat's daemon-side state (which includes 'cold')
        # to the dispatcher-side mirror state (which collapses 'cold'
        # into READY-when-warmed semantics — a cold-but-alive daemon
        # is still reachable; the dispatcher's threshold check still
        # gates it correctly via queue_depth).
        mirror_state = DaemonState.READY if state == "cold" else DaemonState(state)
        with self._lock:
            self._queue_states[queue] = mirror_state
            self._queue_depths[queue] = queue_depth
            if (
                self._last_heartbeat_ts is None
                or ts_monotonic > self._last_heartbeat_ts
            ):
                self._last_heartbeat_ts = ts_monotonic

    # ------------------------------------------------------------------
    # Outputs (dispatcher → mirror)
    # ------------------------------------------------------------------

    def state(self, queue: str) -> DaemonState:
        """Return the mirror state for a queue. UNRESPONSIVE if the
        queue has never published a heartbeat — the cold-start
        contract."""
        with self._lock:
            return self._queue_states.get(queue, DaemonState.UNRESPONSIVE)

    def queue_depth(self, queue: str) -> int:
        """Return the most recent ``queue_depth`` reported for a queue,
        or 0 if no heartbeat was ever seen."""
        with self._lock:
            return self._queue_depths.get(queue, 0)

    def last_heartbeat_ts(self) -> float | None:
        """Return the most recent ``ts_monotonic`` across any queue, or
        ``None`` if no heartbeat was ever recorded."""
        with self._lock:
            return self._last_heartbeat_ts

    def is_unresponsive(self, *, now_monotonic: float | None = None) -> bool:
        """Return True if the elapsed gap since the last heartbeat
        exceeds ``2 × heartbeat_interval`` (the explicit Felix
        13-minute-silence window).

        :param now_monotonic: override for tests; defaults to
            ``time.monotonic()``.
        """
        if self._force_unresponsive:
            return True
        with self._lock:
            last = self._last_heartbeat_ts
        if last is None:
            return True
        if now_monotonic is None:
            now_monotonic = time.monotonic()
        return (now_monotonic - last) > (2.0 * self._heartbeat_interval)

    # ------------------------------------------------------------------
    # Test hooks
    # ------------------------------------------------------------------

    def force_unresponsive_for_test(self) -> None:
        """Force :meth:`is_unresponsive` to return True regardless of
        the wall clock. Lets tests exercise the fallback branch
        deterministically without sleeping for the heartbeat window.
        """
        self._force_unresponsive = True

    def clear_for_test(self) -> None:
        """Reset all state. Useful in fixtures so tests don't bleed
        heartbeat history between cases."""
        with self._lock:
            self._queue_states.clear()
            self._queue_depths.clear()
            self._last_heartbeat_ts = None
            self._force_unresponsive = False


def get_mirror() -> DaemonStateMirror:
    """Return the process-wide :class:`DaemonStateMirror` singleton.

    Pinned to a builtins attribute so it survives ``uvicorn --reload``
    re-imports of this module within the same interpreter (same
    pattern as ``sidequest.telemetry.watcher_hub``)."""
    existing = getattr(builtins, _BUILTINS_MIRROR_ATTR, None)
    if isinstance(existing, DaemonStateMirror):
        return existing
    mirror = DaemonStateMirror()
    setattr(builtins, _BUILTINS_MIRROR_ATTR, mirror)
    return mirror


__all__ = [
    "DaemonState",
    "DaemonStateMirror",
    "get_mirror",
]
