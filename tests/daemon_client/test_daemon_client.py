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
    ) -> None:
        self.reply = reply or {}
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
            reply = {"id": req.get("id"), **self.reply}
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
