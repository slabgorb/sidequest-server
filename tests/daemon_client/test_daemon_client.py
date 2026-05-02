"""Unit tests for the async daemon JSON-RPC client.

Spins up an in-process Unix-socket server that speaks the daemon's
line-framed JSON protocol, so we can exercise the real socket path
without depending on a running sidequest-renderer process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from sidequest.daemon_client import (
    MAX_EMBED_BYTES,
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
        # Signals fired when the handler first receives a request for the
        # named method, before any configured delay is applied. Tests use
        # these to gate follow-on concurrent requests on "the slow handler
        # is actually running" rather than guessing with a wall-clock sleep.
        self.method_entered: dict[str, asyncio.Event] = {}
        self._server: asyncio.AbstractServer | None = None
        self._path: Path | None = None

    async def start(self, path: Path) -> None:
        self._path = path
        self._server = await asyncio.start_unix_server(self._handle, path=str(path))

    def signal_for(self, method: str) -> asyncio.Event:
        """Return (lazily creating) the Event fired when `method` is handled."""
        if method not in self.method_entered:
            self.method_entered[method] = asyncio.Event()
        return self.method_entered[method]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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
            if method in self.method_entered:
                self.method_entered[method].set()
            delay = self.delays_by_method.get(method)
            if delay is not None:
                await asyncio.sleep(delay)
            reply_body = self.replies_by_method.get(method, self.reply)
            reply = {"id": req.get("id"), **reply_body}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

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
    daemon = _FakeDaemon(reply={"error": {"code": "GENERATION_FAILED", "message": "boom"}})
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
    daemon = _FakeDaemon(reply={"error": {"code": "EMBED_FAILED", "message": "model offline"}})
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
async def test_embed_empty_text_surfaces_invalid_request(short_sock: Path) -> None:
    """The daemon rejects empty text with INVALID_REQUEST; the client must
    surface that as a DaemonRequestError with the same code rather than
    swallowing it or returning a default."""
    daemon = _FakeDaemon(reply={"error": {"code": "INVALID_REQUEST", "message": "empty text"}})
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonRequestError) as excinfo:
            await client.embed("")
    finally:
        await daemon.stop()
    assert excinfo.value.code == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_embed_rejects_oversized_text_before_network_call(
    short_sock: Path,
) -> None:
    """The client enforces MAX_EMBED_BYTES locally so a runaway caller can't
    block the event loop on json.dumps or balloon the daemon's readline
    buffer. The daemon is never contacted — the socket path doesn't even
    need to exist for this guard to fire."""
    client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
    oversized = "a" * (MAX_EMBED_BYTES + 1)
    with pytest.raises(ValueError, match="UTF-8 limit"):
        await client.embed(oversized)


@pytest.mark.asyncio
async def test_client_issues_concurrent_render_and_embed_connections(
    short_sock: Path,
) -> None:
    """Verifies the *client-side* concurrency property only.

    A slow render in flight must not stall a concurrent embed call at
    ``DaemonClient`` itself — i.e. the client opens an independent socket
    connection per request rather than serializing calls through a shared
    resource.

    Intentionally does **not** verify daemon-side lock behavior. The fake
    daemon used here (_FakeDaemon) processes each connection in its own
    coroutine with no shared lock, so it cannot model the render_lock /
    embed_lock split introduced by story 37-23. True end-to-end 37-23
    verification requires an integration test against a live sidequest
    daemon and is tracked as a follow-up delivery finding.
    """
    slow_render_delay = 2.0
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
    render_started = daemon.signal_for("render")
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=5.0)
        render_task = asyncio.create_task(
            client.render({"tier": "scene_illustration", "subject": "x"})
        )
        # Wait for the fake daemon to confirm the render handler is running
        # before firing embed. This replaces a wall-clock guess with a real
        # synchronisation point: we know render is genuinely in flight when
        # the event fires.
        await asyncio.wait_for(render_started.wait(), timeout=2.0)
        embed_start = time.monotonic()
        embed_result = await client.embed("short fragment")
        embed_elapsed = time.monotonic() - embed_start
        render_result = await render_task
    finally:
        await daemon.stop()

    assert embed_result["embedding"] == [0.4, 0.5, 0.6]
    assert render_result["image_url"] == "/tmp/x.png"
    # With a 2s render delay, an embed that completes in anything under
    # ~1s proves the client is not serializing behind render. The margin
    # is generous so scheduler jitter on loaded CI does not cause spurious
    # failures.
    assert embed_elapsed < slow_render_delay / 2, (
        f"embed took {embed_elapsed:.3f}s — client appears to be serializing "
        f"behind the {slow_render_delay}s render in flight"
    )
    methods = [req["method"] for req in daemon.requests]
    assert "render" in methods
    assert "embed" in methods


# ---------------------------------------------------------------------------
# Round-5: INVALID_RESPONSE runtime validation (Story 37-33 round-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_missing_embedding_key_surfaces_invalid_response(
    short_sock: Path,
) -> None:
    """A malformed reply missing ``embedding`` raises INVALID_RESPONSE, not KeyError."""
    daemon = _FakeDaemon(reply={"result": {"model": "m", "latency_ms": 1}})
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonRequestError) as exc:
            await client.embed("text")
    finally:
        await daemon.stop()
    assert exc.value.code == "INVALID_RESPONSE"
    assert "embedding" in exc.value.message


@pytest.mark.asyncio
async def test_embed_zero_length_embedding_surfaces_invalid_response(
    short_sock: Path,
) -> None:
    """HIGH fix: [] would otherwise propagate to requeue_dimension_mismatched(0)
    and wipe the entire store. Refuse at the client boundary."""
    daemon = _FakeDaemon(reply={"result": {"embedding": [], "model": "m", "latency_ms": 1}})
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonRequestError) as exc:
            await client.embed("text")
    finally:
        await daemon.stop()
    assert exc.value.code == "INVALID_RESPONSE"
    assert "zero-length" in exc.value.message


@pytest.mark.asyncio
async def test_embed_non_list_embedding_surfaces_invalid_response(
    short_sock: Path,
) -> None:
    daemon = _FakeDaemon(reply={"result": {"embedding": "oops", "model": "m", "latency_ms": 1}})
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonRequestError) as exc:
            await client.embed("text")
    finally:
        await daemon.stop()
    assert exc.value.code == "INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_embed_bool_elements_surface_invalid_response(
    short_sock: Path,
) -> None:
    """``bool`` is a subclass of ``int``; a daemon returning ``[True, False]``
    must not silently pass as a valid 1.0 / 0.0 embedding."""
    daemon = _FakeDaemon(
        reply={"result": {"embedding": [True, False, True], "model": "m", "latency_ms": 1}}
    )
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonRequestError) as exc:
            await client.embed("text")
    finally:
        await daemon.stop()
    assert exc.value.code == "INVALID_RESPONSE"
    assert "non-numeric" in exc.value.message


@pytest.mark.asyncio
async def test_embed_wrong_model_type_surfaces_invalid_response(
    short_sock: Path,
) -> None:
    daemon = _FakeDaemon(reply={"result": {"embedding": [0.1], "model": 42, "latency_ms": 1}})
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        with pytest.raises(DaemonRequestError) as exc:
            await client.embed("text")
    finally:
        await daemon.stop()
    assert exc.value.code == "INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_embed_mixed_int_float_embedding_accepted(short_sock: Path) -> None:
    """Valid numeric elements (int or float, but not bool) round-trip cleanly."""
    daemon = _FakeDaemon(
        reply={"result": {"embedding": [1, 0.5, 0], "model": "m", "latency_ms": 1}}
    )
    await daemon.start(short_sock)
    try:
        client = DaemonClient(socket_path=short_sock, timeout_seconds=2.0)
        response = await client.embed("text")
    finally:
        await daemon.stop()
    assert response["embedding"] == [1, 0.5, 0]
