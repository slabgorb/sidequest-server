"""Wiring test: session handler dispatches a render when the narrator
flags a visual scene.

Exercises :meth:`WebSocketSessionHandler._maybe_dispatch_render` against
a real ``DaemonClient`` talking to an in-process Unix-socket fake
daemon. Asserts:

1. A ``RENDER_QUEUED`` message is returned in the turn's outbound frames.
2. The render task fires a request to the daemon.
3. The daemon's reply is translated into an ``IMAGE`` message posted to
   the connection's outbound queue.
4. With the feature flag off, no render fires and no RENDER_QUEUED ships.

This is the wiring test CLAUDE.md requires — the unit tests in
``test_daemon_client.py`` cover the client alone; this one proves the
full pipeline from narration-result → daemon → UI message.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def short_sock(tmp_path: Path) -> Path:
    """Short Unix-socket path (macOS caps sun_path ~104 bytes; pytest's
    tmp_path blows past it). Cleaned up after the test."""
    del tmp_path
    p = Path(f"/tmp/sq-render-test-{uuid.uuid4().hex[:8]}.sock")
    yield p
    if p.exists():
        p.unlink()

from sidequest.agents.orchestrator import NarrationTurnResult, VisualScene
from sidequest.protocol.enums import MessageType
from sidequest.server.session_handler import (
    WebSocketSessionHandler,
    _SessionData,
)


class _FakeDaemon:
    """Unix-domain echo server matching the daemon protocol."""

    def __init__(self, reply_payload: dict[str, Any]) -> None:
        self.reply_payload = reply_payload
        self.requests: list[dict[str, Any]] = []
        self._server: asyncio.AbstractServer | None = None
        self._ready = asyncio.Event()

    async def start(self, path: Path) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=str(path))
        self._ready.set()

    async def _handle(self, reader, writer) -> None:  # noqa: ANN001
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line.decode())
            self.requests.append(req)
            reply = {"id": req.get("id"), "result": self.reply_payload}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


def _make_session_data(player_id: str = "p-1") -> _SessionData:
    from unittest.mock import MagicMock

    from sidequest.game.session import GameSnapshot, TurnManager

    snap = GameSnapshot(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        location="Tood's Dome — Nest Crack",
        turn_manager=TurnManager(interaction=3),
    )
    sd = _SessionData(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        player_name="Rux",
        player_id=player_id,
        snapshot=snap,
        store=MagicMock(),
        genre_pack=MagicMock(),
        orchestrator=MagicMock(),
    )
    return sd


def _make_handler_with_queue() -> tuple[WebSocketSessionHandler, asyncio.Queue]:
    handler = WebSocketSessionHandler(save_dir=Path("/tmp/never-used"))
    queue: asyncio.Queue[object] = asyncio.Queue()
    handler._out_queue = queue  # noqa: SLF001 — test wiring
    return handler, queue


@pytest.mark.asyncio
async def test_render_dispatch_fires_daemon_and_enqueues_image(
    tmp_path: Path, short_sock: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock = short_sock
    daemon = _FakeDaemon(
        reply_payload={
            "image_url": str(tmp_path / "render_abc.png"),
            "width": 1024,
            "height": 768,
            "elapsed_ms": 4200,
        }
    )
    await daemon.start(sock)

    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
    monkeypatch.setenv("SIDEQUEST_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "sidequest.server.session_handler.DaemonClient",
        lambda: _client_bound_to(sock),
    )

    handler, queue = _make_handler_with_queue()
    sd = _make_session_data()
    result = NarrationTurnResult(
        narration="The crack yawns open.",
        visual_scene=VisualScene(
            subject="a jagged fissure in red rock",
            tier="scene_illustration",
            mood="ominous",
            tags=["desert", "ruin"],
        ),
    )

    queued = handler._maybe_dispatch_render(sd, result)  # noqa: SLF001
    assert queued is not None
    assert queued.type == MessageType.RENDER_QUEUED
    render_id = queued.payload.render_id
    assert len(render_id) == 12

    # Drain the background render coroutine.
    image_msg = await asyncio.wait_for(queue.get(), timeout=2.0)
    await daemon.stop()

    assert image_msg.type == MessageType.IMAGE
    assert image_msg.payload.render_id == render_id
    assert image_msg.payload.url == "/renders/render_abc.png"
    assert image_msg.payload.width == 1024
    assert image_msg.payload.tier == "scene_illustration"

    # Daemon saw the full request with narrator-derived fields.
    assert len(daemon.requests) == 1
    req = daemon.requests[0]
    assert req["method"] == "render"
    assert req["params"]["subject"] == "a jagged fissure in red rock"
    assert req["params"]["tier"] == "scene_illustration"
    assert req["params"]["mood"] == "ominous"
    assert req["params"]["tags"] == ["desert", "ruin"]
    assert req["params"]["location"] == "Tood's Dome — Nest Crack"
    assert req["params"]["genre"] == "mutant_wasteland"


@pytest.mark.asyncio
async def test_render_skipped_when_flag_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SIDEQUEST_RENDER_ENABLED", raising=False)
    handler, queue = _make_handler_with_queue()
    sd = _make_session_data()
    result = NarrationTurnResult(
        narration="...",
        visual_scene=VisualScene(subject="x", tier="scene_illustration"),
    )
    assert handler._maybe_dispatch_render(sd, result) is None  # noqa: SLF001
    assert queue.empty()


@pytest.mark.asyncio
async def test_render_skipped_when_no_visual_scene(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
    handler, queue = _make_handler_with_queue()
    sd = _make_session_data()
    result = NarrationTurnResult(narration="flat text turn")
    assert handler._maybe_dispatch_render(sd, result) is None  # noqa: SLF001
    assert queue.empty()


@pytest.mark.asyncio
async def test_render_skipped_when_daemon_socket_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
    missing = tmp_path / "never-created.sock"
    monkeypatch.setattr(
        "sidequest.server.session_handler.DaemonClient",
        lambda: _client_bound_to(missing),
    )
    handler, queue = _make_handler_with_queue()
    sd = _make_session_data()
    result = NarrationTurnResult(
        narration="...",
        visual_scene=VisualScene(subject="x", tier="scene_illustration"),
    )
    assert handler._maybe_dispatch_render(sd, result) is None  # noqa: SLF001
    assert queue.empty()


def _client_bound_to(path: Path):
    """Return a DaemonClient fixed on a given socket path — used to swap
    the default-constructed client in the handler."""
    from sidequest.daemon_client import DaemonClient

    return DaemonClient(socket_path=path, timeout_seconds=2.0)


@pytest.mark.asyncio
async def test_render_dispatch_self_heals_after_daemon_restart(
    tmp_path: Path, short_sock: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4-BUG wiring test (CLAUDE.md mandate).

    Simulates the playtest 2026-04-26 failure: server boots with the OLD
    daemon's tmp dir mounted. Daemon restarts; its NEW tmp dir is
    different. A render-completed reply now lands with image_url under
    the new dir.

    Without the fix: ``_render_url_from_path`` falls through (path not
    under SIDEQUEST_OUTPUT_DIR), the IMAGE message ships an absolute
    filesystem path, the UI 404s on it.

    With the fix: ``ensure_render_mount`` registers the new dir on the
    live mount and the IMAGE message ships a clean ``/renders/...`` URL
    that an HTTP GET against the live app actually serves.
    """
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient

    from sidequest.server import render_mounts

    # OLD daemon dir (env points here at startup; it's empty/stale).
    old_dir = tmp_path / "sq-daemon-OLD"
    old_dir.mkdir()
    monkeypatch.setenv("SIDEQUEST_OUTPUT_DIR", str(old_dir))

    # NEW daemon dir (post-restart) with a real image file.
    new_dir = tmp_path / "sq-daemon-NEW" / "zimage"
    new_dir.mkdir(parents=True)
    image_file = new_dir / "render_post_restart.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\nactual-bytes")

    # Daemon reply uses the NEW path.
    daemon = _FakeDaemon(
        reply_payload={
            "image_url": str(image_file),
            "width": 512,
            "height": 512,
            "elapsed_ms": 1234,
        }
    )
    await daemon.start(short_sock)

    # Build a minimal app that mirrors create_app's mount + active-app
    # registration so the heal code path can find the live mount.
    app = FastAPI()
    app.mount(
        "/renders",
        StaticFiles(directory=str(old_dir)),
        name="render_assets",
    )
    render_mounts.set_active_app(app)
    try:
        monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
        monkeypatch.setattr(
            "sidequest.server.session_handler.DaemonClient",
            lambda: _client_bound_to(short_sock),
        )

        handler, queue = _make_handler_with_queue()
        sd = _make_session_data()
        result = NarrationTurnResult(
            narration="The new tmpdir's pixels.",
            visual_scene=VisualScene(
                subject="post-restart scene",
                tier="scene_illustration",
            ),
        )

        queued = handler._maybe_dispatch_render(sd, result)  # noqa: SLF001
        assert queued is not None
        image_msg = await asyncio.wait_for(queue.get(), timeout=2.0)
        await daemon.stop()

        assert image_msg.type == MessageType.IMAGE
        # The URL must be a clean /renders/* path (NOT an absolute
        # filesystem path with the leading slash of /var or /private).
        url = image_msg.payload.url
        assert url.startswith("/renders/"), (
            f"expected /renders/* URL, got absolute path: {url!r} — "
            f"the self-healing mount didn't fire"
        )
        assert url.endswith("render_post_restart.png")

        # Wiring proof: an HTTP GET against the live app actually serves
        # the file from the NEW dir.
        client = TestClient(app)
        resp = client.get(url)
        assert resp.status_code == 200, (
            f"GET {url} returned {resp.status_code}: "
            f"healed mount didn't make the file reachable"
        )
        assert resp.content == b"\x89PNG\r\n\x1a\nactual-bytes"
    finally:
        render_mounts.reset_for_app(app)
        render_mounts.set_active_app(None)
