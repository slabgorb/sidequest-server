"""RED tests — Story 45-31 — server-side ``DaemonStateMirror``.

The heartbeat reader (server-side, threaded into ``DaemonClient``)
populates a ``DaemonStateMirror`` from incoming ``{"event":"heartbeat",
"queue":..., "state":..., "queue_depth":..., "ts_monotonic":...}`` events.
``_maybe_dispatch_render`` consults the mirror to decide whether the
daemon is reachable, busy, paused, or unresponsive — the mirror is the
load-bearing replacement for the binary
``client.is_available()`` socket-on-disk check at
``websocket_session_handler.py:3258``.

These tests pin the mirror's behavioral contract:
- a fresh mirror starts in the ``UNRESPONSIVE`` state — no heartbeat seen
  yet means "do not assume the daemon is alive."
- ``record_heartbeat`` advances the mirror's per-queue state.
- ``last_heartbeat_ts`` updates on every heartbeat event.
- ``is_unresponsive`` returns True when the wall-clock gap exceeds
  ``2 × heartbeat_interval`` since the last received heartbeat (per the
  story's "lost-heartbeat window" wire-first contract).

These are unit tests for a new module that does not yet exist
(``sidequest.daemon_client.state_mirror``); they will fail at import
collection until Dev lands the module.
"""

from __future__ import annotations

import pytest


def test_state_mirror_module_is_importable() -> None:
    """The ``state_mirror`` submodule must live under ``sidequest.daemon_client``
    so the heartbeat reader can populate it without a circular import.

    This is the wiring guard — it MUST RED until Dev lands the module.
    """
    from sidequest.daemon_client import state_mirror  # noqa: F401


def test_daemon_state_enum_has_four_states() -> None:
    """``DaemonState`` enumerates the four mirror states the dispatcher
    branches on: READY (idle, accept work), BUSY (work in flight),
    PAUSED (GPU coordinator gated the queue), UNRESPONSIVE (no
    heartbeat past 2×interval — degrade gracefully)."""
    from sidequest.daemon_client.state_mirror import DaemonState

    assert DaemonState.READY.value == "ready"
    assert DaemonState.BUSY.value == "busy"
    assert DaemonState.PAUSED.value == "paused"
    assert DaemonState.UNRESPONSIVE.value == "unresponsive"


def test_fresh_mirror_starts_unresponsive() -> None:
    """A fresh mirror has never seen a heartbeat — it MUST default to
    UNRESPONSIVE so the dispatcher fails closed (not open) before the
    daemon's first heartbeat lands. This is the no-silent-fallback
    contract: silence is degradation, not health."""
    from sidequest.daemon_client.state_mirror import DaemonState, DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)

    assert mirror.state("image") == DaemonState.UNRESPONSIVE
    assert mirror.last_heartbeat_ts() is None


def test_record_heartbeat_advances_per_queue_state() -> None:
    """``record_heartbeat`` updates the mirror's per-queue state. The
    ``image`` and ``embed`` queues are independent — a busy embed must
    NOT mark the image queue busy (per ADR-035 + 37-23 lock split).

    Also exercises ``queue_depth()`` — review M8 flagged the previous
    version of this test for not asserting the depth round-trip."""
    from sidequest.daemon_client.state_mirror import DaemonState, DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)

    mirror.record_heartbeat(queue="image", state="ready", queue_depth=0, ts_monotonic=100.0)
    assert mirror.state("image") == DaemonState.READY
    assert mirror.queue_depth("image") == 0
    # embed queue must still be UNRESPONSIVE — no heartbeat seen for it.
    assert mirror.state("embed") == DaemonState.UNRESPONSIVE
    assert mirror.queue_depth("embed") == 0  # default for unknown queue

    mirror.record_heartbeat(queue="embed", state="busy", queue_depth=1, ts_monotonic=101.0)
    assert mirror.state("embed") == DaemonState.BUSY
    assert mirror.queue_depth("embed") == 1
    # image is untouched.
    assert mirror.state("image") == DaemonState.READY
    assert mirror.queue_depth("image") == 0

    # A second heartbeat for the same queue with a higher depth must
    # update queue_depth() — catches a regression where queue_depth is
    # frozen on first record.
    mirror.record_heartbeat(queue="embed", state="busy", queue_depth=3, ts_monotonic=102.0)
    assert mirror.queue_depth("embed") == 3


def test_record_heartbeat_updates_last_seen_ts() -> None:
    """``last_heartbeat_ts`` returns the daemon's ``ts_monotonic`` from
    the most recent heartbeat — for diagnostic display only. The
    server-side receive ts (from ``last_received_at``) is what the
    dispatcher's staleness check uses (review H3)."""
    from sidequest.daemon_client.state_mirror import DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)

    mirror.record_heartbeat(
        queue="image", state="ready", queue_depth=0,
        ts_monotonic=100.0, now_monotonic=1000.0,
    )
    assert mirror.last_heartbeat_ts() == pytest.approx(100.0)
    assert mirror.last_received_at() == pytest.approx(1000.0)

    mirror.record_heartbeat(
        queue="image", state="busy", queue_depth=1,
        ts_monotonic=105.5, now_monotonic=1005.5,
    )
    assert mirror.last_heartbeat_ts() == pytest.approx(105.5)
    assert mirror.last_received_at() == pytest.approx(1005.5)


def test_is_unresponsive_after_2x_interval_gap() -> None:
    """``is_unresponsive(now)`` returns True when
    ``now - last_received_at`` exceeds ``2 × heartbeat_interval`` —
    the explicit "Felix 13-minute silence" window the story is
    designed to surface. ``now`` and ``last_received_at`` share the
    server's monotonic clock reference."""
    from sidequest.daemon_client.state_mirror import DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)
    # Record with a server-side receive ts of 100.0 — the daemon's
    # ts_monotonic is irrelevant to is_unresponsive.
    mirror.record_heartbeat(
        queue="image", state="ready", queue_depth=0,
        ts_monotonic=100.0, now_monotonic=100.0,
    )

    # 60s elapsed — exactly 2× interval — boundary case, still considered alive.
    assert mirror.is_unresponsive(now_monotonic=160.0) is False

    # 61s elapsed — past 2× interval — UNRESPONSIVE.
    assert mirror.is_unresponsive(now_monotonic=161.0) is True


def test_is_unresponsive_ignores_daemon_clock_skew() -> None:
    """Review H3 regression: the daemon and server are separate
    processes with independent ``time.monotonic()`` references. The
    mirror MUST NOT use the daemon's ``ts_monotonic`` for staleness
    arithmetic. A daemon emitting heartbeats with ``ts_monotonic`` far
    in the past or far in the future MUST NOT cause spurious
    UNRESPONSIVE / RESPONSIVE flips on the server side.
    """
    from sidequest.daemon_client.state_mirror import DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)

    # Daemon's ts_monotonic is "billions of seconds ago" relative to
    # server's clock — common when the daemon was rebooted minutes
    # ago and the server was rebooted weeks ago. The mirror's
    # is_unresponsive MUST decide based on server-side receive ts,
    # not the daemon's wildly different reference.
    mirror.record_heartbeat(
        queue="image",
        state="ready",
        queue_depth=0,
        ts_monotonic=-1e9,  # daemon claims its monotonic is far-past
        now_monotonic=500.0,  # server received this heartbeat 0.5ms ago
    )
    # 1 second after receive — well inside the 60s threshold.
    assert mirror.is_unresponsive(now_monotonic=501.0) is False, (
        "mirror used daemon's ts_monotonic for staleness — that field "
        "is from a different process's clock and must not feed the gap "
        "calculation"
    )

    # Conversely: a daemon emitting a far-future ts_monotonic must not
    # mask staleness when the server-side receive ts is actually old.
    mirror.record_heartbeat(
        queue="image",
        state="ready",
        queue_depth=0,
        ts_monotonic=1e9,  # daemon claims wildly future monotonic
        now_monotonic=600.0,  # server-side receive
    )
    assert mirror.is_unresponsive(now_monotonic=700.0) is True, (
        "mirror gave the daemon's far-future ts_monotonic credit "
        "for staleness — that field is informational only"
    )


def test_is_unresponsive_when_never_seen_heartbeat() -> None:
    """A mirror that never received a heartbeat is unresponsive
    regardless of clock — this is the cold-start contract."""
    from sidequest.daemon_client.state_mirror import DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)

    assert mirror.is_unresponsive(now_monotonic=0.0) is True
    assert mirror.is_unresponsive(now_monotonic=10_000.0) is True


def test_record_heartbeat_rejects_invalid_state() -> None:
    """An unknown state string MUST raise — the daemon protocol contract
    is stable, and a typo in a daemon-side emit is a bug we want to
    catch loudly (no silent fallback to a default state)."""
    from sidequest.daemon_client.state_mirror import DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)

    with pytest.raises(ValueError):
        mirror.record_heartbeat(
            queue="image", state="nonsense", queue_depth=0, ts_monotonic=1.0
        )


def test_mirror_singleton_accessor_is_module_level() -> None:
    """The dispatcher and the heartbeat reader BOTH need to reach the
    same mirror instance. The module exposes a ``get_mirror()`` accessor
    that returns the process-wide singleton.

    Wiring guard — this is the seam the dispatcher will consult at
    ``_maybe_dispatch_render``; the heartbeat reader populates the same
    instance. If these diverge, the dispatcher reads from a mirror that
    nothing writes to, and the Felix anti-silence test passes vacuously.
    """
    from sidequest.daemon_client.state_mirror import DaemonStateMirror, get_mirror

    a = get_mirror()
    b = get_mirror()
    assert isinstance(a, DaemonStateMirror)
    assert a is b, "get_mirror() must return the same singleton on every call"
