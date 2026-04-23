"""Async JSON-RPC client for the sidequest media daemon.

Speaks the line-framed JSON protocol hosted by
``sidequest_daemon.media.daemon`` over a Unix domain socket. A connection
is opened per request for simplicity and per-request fault isolation
(one broken render doesn't poison a persistent socket that a dozen other
turns are sharing).

The daemon is an *optional* sidecar: if the socket does not exist, if the
daemon rejects the connection, or if the response times out, the client
surfaces a structured :class:`DaemonUnavailableError` so callers can
emit a clear OTEL span (``render.socket_unavailable`` / ``render.timeout``)
and move on — renders are best-effort, text-only play must continue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("sidequest.daemon_client")

DEFAULT_SOCKET_PATH = Path("/tmp/sidequest-renderer.sock")
DEFAULT_TIMEOUT_SECONDS = 180.0
"""Z-Image renders take 10-60s on M-series; 3 min is a generous cap."""


class DaemonClientError(Exception):
    """Base class for daemon client errors."""


class DaemonUnavailableError(DaemonClientError):
    """The daemon socket is absent or unreachable. Caller should log a
    span and carry on without a render — the daemon is optional."""


class DaemonRequestError(DaemonClientError):
    """The daemon accepted the request but returned a structured error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class DaemonClient:
    """Async JSON-RPC client speaking the daemon's line-framed protocol."""

    def __init__(
        self,
        socket_path: Path | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._socket_path = socket_path or DEFAULT_SOCKET_PATH
        self._timeout = timeout_seconds

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def is_available(self) -> bool:
        """Fast check: does the socket exist on disk? The daemon creates
        the sockfile eagerly on startup. This is cheaper than opening a
        connection for every turn just to detect an absent daemon."""
        return self._socket_path.exists()

    async def render(self, params: dict[str, Any]) -> dict[str, Any]:
        """Send a render request and return the result dict.

        :raises DaemonUnavailableError: socket missing, connection refused,
            or response timed out.
        :raises DaemonRequestError: daemon returned ``{"error": {...}}``.
        """
        return await self._call("render", params)

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = uuid.uuid4().hex[:12]
        with tracer.start_as_current_span("daemon_client.request") as span:
            span.set_attribute("daemon.method", method)
            span.set_attribute("daemon.request_id", request_id)
            span.set_attribute("daemon.tier", str(params.get("tier", "")))
            if not self.is_available():
                span.set_attribute("daemon.outcome", "socket_missing")
                raise DaemonUnavailableError(
                    f"daemon socket not found at {self._socket_path}"
                )
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(path=str(self._socket_path)),
                    timeout=self._timeout,
                )
            except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
                span.set_attribute("daemon.outcome", "connection_failed")
                span.set_attribute("daemon.error", type(exc).__name__)
                raise DaemonUnavailableError(str(exc)) from exc
            except asyncio.TimeoutError as exc:
                span.set_attribute("daemon.outcome", "connect_timeout")
                raise DaemonUnavailableError(
                    f"timed out opening daemon socket after {self._timeout}s"
                ) from exc

            try:
                req_line = json.dumps(
                    {"id": request_id, "method": method, "params": params}
                ) + "\n"
                writer.write(req_line.encode())
                await writer.drain()
                try:
                    raw = await asyncio.wait_for(
                        reader.readline(), timeout=self._timeout
                    )
                except asyncio.TimeoutError as exc:
                    span.set_attribute("daemon.outcome", "reply_timeout")
                    raise DaemonUnavailableError(
                        f"daemon did not reply within {self._timeout}s"
                    ) from exc
                if not raw:
                    span.set_attribute("daemon.outcome", "eof_before_reply")
                    raise DaemonUnavailableError(
                        "daemon closed socket before sending a reply"
                    )
                try:
                    reply = json.loads(raw.decode().strip())
                except json.JSONDecodeError as exc:
                    span.set_attribute("daemon.outcome", "invalid_json")
                    raise DaemonUnavailableError(
                        f"daemon sent non-JSON reply: {exc}"
                    ) from exc
                if "error" in reply and reply["error"] is not None:
                    code = str(reply["error"].get("code", "UNKNOWN"))
                    msg = str(reply["error"].get("message", ""))
                    span.set_attribute("daemon.outcome", "error")
                    span.set_attribute("daemon.error_code", code)
                    raise DaemonRequestError(code, msg)
                result = reply.get("result") or {}
                span.set_attribute("daemon.outcome", "ok")
                return dict(result)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass


def render_enabled() -> bool:
    """``SIDEQUEST_RENDER_ENABLED=1`` opts a process into daemon dispatch.

    Default off so the test suite doesn't accidentally spin up a socket
    connection on every narration turn. The production server sets this
    in its systemd / launchd unit.
    """
    return os.environ.get("SIDEQUEST_RENDER_ENABLED", "").strip() in {
        "1",
        "true",
        "yes",
        "on",
    }
