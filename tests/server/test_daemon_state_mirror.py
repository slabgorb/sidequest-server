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
    NOT mark the image queue busy (per ADR-035 + 37-23 lock split)."""
    from sidequest.daemon_client.state_mirror import DaemonState, DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)

    mirror.record_heartbeat(queue="image", state="ready", queue_depth=0, ts_monotonic=100.0)
    assert mirror.state("image") == DaemonState.READY
    # embed queue must still be UNRESPONSIVE — no heartbeat seen for it.
    assert mirror.state("embed") == DaemonState.UNRESPONSIVE

    mirror.record_heartbeat(queue="embed", state="busy", queue_depth=1, ts_monotonic=101.0)
    assert mirror.state("embed") == DaemonState.BUSY
    # image is untouched.
    assert mirror.state("image") == DaemonState.READY


def test_record_heartbeat_updates_last_seen_ts() -> None:
    """``last_heartbeat_ts`` returns the most recent ``ts_monotonic`` the
    mirror has seen across any queue. The dispatcher uses this to
    populate ``render.unavailable``'s ``last_heartbeat_ts`` attribute
    when it falls back."""
    from sidequest.daemon_client.state_mirror import DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)

    mirror.record_heartbeat(queue="image", state="ready", queue_depth=0, ts_monotonic=100.0)
    assert mirror.last_heartbeat_ts() == pytest.approx(100.0)

    mirror.record_heartbeat(queue="image", state="busy", queue_depth=1, ts_monotonic=105.5)
    assert mirror.last_heartbeat_ts() == pytest.approx(105.5)


def test_is_unresponsive_after_2x_interval_gap() -> None:
    """``is_unresponsive(now)`` returns True when ``now - last_heartbeat_ts``
    exceeds ``2 × heartbeat_interval`` — the explicit "Felix 13-minute
    silence" window the story is designed to surface."""
    from sidequest.daemon_client.state_mirror import DaemonStateMirror

    mirror = DaemonStateMirror(heartbeat_interval_seconds=30.0)
    mirror.record_heartbeat(queue="image", state="ready", queue_depth=0, ts_monotonic=100.0)

    # 60s elapsed — exactly 2× interval — boundary case, still considered alive.
    assert mirror.is_unresponsive(now_monotonic=160.0) is False

    # 61s elapsed — past 2× interval — UNRESPONSIVE.
    assert mirror.is_unresponsive(now_monotonic=161.0) is True


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
