"""Unit tests for the async daemon JSON-RPC client.

Spins up an in-process Unix-socket server that speaks the daemon's
line-framed JSON protocol, so we can exercise the real socket path
without depending on a running sidequest-renderer process.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from sidequest.daemon_client import (
    DaemonClient,
    DaemonRequestError,
    DaemonUnavailableError,
    render_enabled,
)


@pytest.fixture
def short_sock(tmp_path: Path) -> Path:
    """Return a short-enough socket path on macOS (AF_UNIX limits sun_path
    to ~104 bytes). pytest's tmp_path under /var/folders blows past it;
    /tmp is fine. Still tie cleanup to tmp_path so we don't pollute /tmp
    across runs."""
    del tmp_path  # not used; just keeps test isolation keyed to the fixture
    p = Path(f"/tmp/sq-daemon-test-{uuid.uuid4().hex[:8]}.sock")
    yield p
    if p.exists():
        p.unlink()


class _FakeDaemon:
    """Minimal Unix-domain echo server matching the daemon's framing."""

    def __init__(
        self,
        reply: dict[str, Any] | None = None,
        *,
        crash: bool = False,
        replies_by_method: dict[str, dict[str, Any]] | None = None,
        delays_by_method: dict[str, float] | None = None,
    ) -> None:
        self.reply = reply or {}
        self.replies_by_method = replies_by_method or {}
        self.delays_by_method = delays_by_method or {}
        self.crash = crash
        self.requests: list[dict[str, Any]] = []
        self._server: asyncio.AbstractServer | None = None
        self._path: Path | None = None

    async def start(self, path: Path) -> None:
        self._path = path
        self._server = await asyncio.start_unix_server(self._handle, path=str(path))

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line.decode().strip())
            self.requests.append(req)
            if self.crash:
                writer.close()
                return
            method = req.get("method", "")
            delay = self.delays_by_method.get(method)
            if delay:
                await asyncio.sleep(delay)
            reply_body = self.replies_by_method.get(method, self.reply)
            reply = {"id": req.get("id"), **reply_body}
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
        if self._path is not None and self._path.exists():
            self._path.unlink()


@pytest.mark.asyncio
async def test_render_round_trip_returns_result(short_sock: Path) -> None:
    daemon = _FakeDaemon(
        reply={"result": {"image_url": "/tmp/x.png", "width": 1024, "height": 768}}
    )
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        result = await client.render({"tier": "scene_illustration", "subject": "x"})
    finally:
        await daemon.stop()
    assert result["image_url"] == "/tmp/x.png"
    assert result["width"] == 1024
    assert daemon.requests[0]["method"] == "render"
    assert daemon.requests[0]["params"]["subject"] == "x"


@pytest.mark.asyncio
async def test_daemon_unavailable_when_socket_missing(short_sock: Path) -> None:
    # short_sock is yielded unlinked — perfect for the absent case.
    client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
    assert not client.is_available()
    with pytest.raises(DaemonUnavailableError):
        await client.render({"tier": "scene_illustration", "subject": "x"})


@pytest.mark.asyncio
async def test_daemon_error_surfaces_structured_exception(short_sock: Path) -> None:
    daemon = _FakeDaemon(
        reply={"error": {"code": "GENERATION_FAILED", "message": "boom"}}
    )
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonRequestError) as excinfo:
            await client.render({"tier": "scene_illustration", "subject": "x"})
    finally:
        await daemon.stop()
    assert excinfo.value.code == "GENERATION_FAILED"
    assert excinfo.value.message == "boom"


@pytest.mark.asyncio
async def test_eof_before_reply_raises_unavailable(short_sock: Path) -> None:
    daemon = _FakeDaemon(crash=True)
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonUnavailableError):
            await client.render({"tier": "scene_illustration", "subject": "x"})
    finally:
        await daemon.stop()


def test_render_enabled_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIDEQUEST_RENDER_ENABLED", raising=False)
    assert render_enabled() is False
    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
    assert render_enabled() is True
    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "false")
    assert render_enabled() is False
    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "yes")
    assert render_enabled() is True


@pytest.mark.asyncio
async def test_embed_round_trip_returns_result(short_sock: Path) -> None:
    daemon = _FakeDaemon(
        reply={
            "result": {
                "embedding": [0.1, 0.2, 0.3],
                "model": "all-MiniLM-L6-v2",
                "latency_ms": 12,
            }
        }
    )
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        result = await client.embed("a fragment of lore")
    finally:
        await daemon.stop()
    assert result["embedding"] == [0.1, 0.2, 0.3]
    assert result["model"] == "all-MiniLM-L6-v2"
    assert result["latency_ms"] == 12
    assert daemon.requests[0]["method"] == "embed"
    assert daemon.requests[0]["params"]["text"] == "a fragment of lore"


@pytest.mark.asyncio
async def test_embed_unavailable_when_socket_missing(short_sock: Path) -> None:
    client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
    assert not client.is_available()
    with pytest.raises(DaemonUnavailableError):
        await client.embed("text")


@pytest.mark.asyncio
async def test_embed_daemon_error_surfaces_structured_exception(
    short_sock: Path,
) -> None:
    daemon = _FakeDaemon(
        reply={"error": {"code": "EMBED_FAILED", "message": "model offline"}}
    )
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonRequestError) as excinfo:
            await client.embed("text")
    finally:
        await daemon.stop()
    assert excinfo.value.code == "EMBED_FAILED"
    assert excinfo.value.message == "model offline"


@pytest.mark.asyncio
async def test_concurrent_render_and_embed_do_not_block_each_other(
    short_sock: Path,
) -> None:
    """Story 37-33 diagnostic.

    Story 37-23 split the daemon's render_lock and embed_lock so an embed
    request (~10ms on CPU) no longer serializes behind a slow render
    (~5–60s on MPS). This asserts the client exposes that independence
    end-to-end: a slow render in flight must not stall a concurrent
    embed call.
    """
    slow_render_delay = 0.25
    daemon = _FakeDaemon(
        replies_by_method={
            "render": {"result": {"image_url": "/tmp/x.png"}},
            "embed": {
                "result": {
                    "embedding": [0.4, 0.5, 0.6],
                    "model": "all-MiniLM-L6-v2",
                    "latency_ms": 8,
                }
            },
        },
        delays_by_method={"render": slow_render_delay},
    )
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        loop = asyncio.get_running_loop()
        render_task = asyncio.create_task(
            client.render({"tier": "scene_illustration", "subject": "x"})
        )
        # Let the slow render get into flight before firing embed so the
        # test exercises the concurrent case, not a serial one.
        await asyncio.sleep(0.02)
        embed_start = loop.time()
        embed_result = await client.embed("short fragment")
        embed_elapsed = loop.time() - embed_start
        render_result = await render_task
    finally:
        await daemon.stop()

    assert embed_result["embedding"] == [0.4, 0.5, 0.6]
    assert render_result["image_url"] == "/tmp/x.png"
    # Embed must finish well before the render delay — the whole point of
    # the 37-23 lock split. Generous margin for CI noise; the real signal
    # is that embed isn't gated on slow_render_delay.
    assert embed_elapsed < slow_render_delay, (
        f"embed took {embed_elapsed:.3f}s — should be independent of the "
        f"{slow_render_delay}s render in flight"
    )
    methods = [req["method"] for req in daemon.requests]
    assert "render" in methods
    assert "embed" in methods
