"""Wiring test for ``watcher_endpoint`` replay-on-connect.

Per CLAUDE.md *"Every Test Suite Needs a Wiring Test"* — proves the
unit-tested ``WatcherHub.replay`` is actually reached from the FastAPI
WebSocket handler a real dashboard hits, not just unit-testable in
isolation.

Pattern: direct handler dispatch with a fake WebSocket, mirroring
``tests/server/test_seat_claim.py`` and ``test_event_log_wiring.py``
which note that ``TestClient.websocket_connect`` "added minutes of
startup time" without buying any extra coverage.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from sidequest.server.watcher import watcher_endpoint
from sidequest.telemetry.watcher_hub import WatcherHub


class _FakeWebSocket:
    """Minimal stand-in for ``fastapi.WebSocket`` — the watcher endpoint
    only calls ``accept``, ``send_json``, and ``receive_text``. The
    receive-loop exits when ``receive_text`` raises
    ``WebSocketDisconnect``, which we trigger after the handler has
    finished its connect-time prelude (hello + replay)."""

    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        # Simulate the client closing the connection so the
        # endpoint's ``while True`` loop exits via
        # ``WebSocketDisconnect`` exactly the way it would in
        # production.
        raise WebSocketDisconnect()


@pytest.mark.asyncio
async def test_endpoint_replays_buffered_events_in_order() -> None:
    """Connecting after events have been published yields, in order:
    hello → replay_start → each buffered event → replay_end → close."""
    hub = WatcherHub()
    hub.bind_loop(asyncio.get_running_loop())
    hub.publish({"event_type": "test_replay_a", "fields": {"i": 1}})
    hub.publish({"event_type": "test_replay_b", "fields": {"i": 2}})
    # Drain run_coroutine_threadsafe callbacks so the buffer holds them.
    for _ in range(5):
        await asyncio.sleep(0)

    ws = _FakeWebSocket()
    await watcher_endpoint(ws, hub)  # type: ignore[arg-type]

    assert ws.accepted is True
    # Frame order:  hello, replay_start, evt_a, evt_b, replay_end.
    assert len(ws.sent) == 5
    names_or_types = [f.get("fields", {}).get("name") or f.get("event_type") for f in ws.sent]
    assert names_or_types == [
        "watcher.connected",
        "watcher.replay_start",
        "test_replay_a",
        "test_replay_b",
        "watcher.replay_end",
    ]
    assert ws.sent[-1]["fields"]["replayed"] == 2


@pytest.mark.asyncio
async def test_endpoint_subscribes_after_replay_so_no_duplicate_during_replay() -> None:
    """Subscribe must happen AFTER replay completes. Otherwise a live
    broadcast that fires during the replay loop would reach the new
    socket twice — once via replay (it's already buffered) and once
    via the live broadcast.

    We assert this by snapshotting the subscribers set the moment
    ``replay`` is invoked: it must be empty for this connection."""
    hub = WatcherHub()
    hub.bind_loop(asyncio.get_running_loop())
    hub.publish({"event_type": "earlier", "fields": {}})
    for _ in range(5):
        await asyncio.sleep(0)

    subscribers_during_replay: list[int] = []
    original_replay = hub.replay

    async def _spy_replay(ws_arg: Any) -> int:
        async with hub._lock:  # noqa: SLF001
            subscribers_during_replay.append(len(hub._subscribers))  # noqa: SLF001
        return await original_replay(ws_arg)

    hub.replay = _spy_replay  # type: ignore[method-assign]

    ws = _FakeWebSocket()
    await watcher_endpoint(ws, hub)  # type: ignore[arg-type]

    assert subscribers_during_replay == [0], (
        f"replay must run with subscribers=0 for this socket; saw {subscribers_during_replay}"
    )


@pytest.mark.asyncio
async def test_endpoint_hello_frame_reports_buffer_depth() -> None:
    """The hello frame's ``hub.stats()`` payload exposes ``buffered``
    so an operator can confirm the bus has history available even
    before the replay starts streaming."""
    hub = WatcherHub()
    hub.bind_loop(asyncio.get_running_loop())
    for i in range(3):
        hub.publish({"event_type": "test_stats", "fields": {"i": i}})
    for _ in range(5):
        await asyncio.sleep(0)

    ws = _FakeWebSocket()
    await watcher_endpoint(ws, hub)  # type: ignore[arg-type]

    hello = ws.sent[0]
    assert hello["fields"]["name"] == "watcher.connected"
    assert hello["fields"]["buffered"] == 3
