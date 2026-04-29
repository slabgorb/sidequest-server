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
import contextlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, TypedDict

from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("sidequest.daemon_client")

DEFAULT_SOCKET_PATH = Path("/tmp/sidequest-renderer.sock")
DEFAULT_TIMEOUT_SECONDS = 180.0
"""Z-Image renders take 10-60s on M-series; 3 min is a generous cap."""

MAX_EMBED_BYTES = 32_768
"""Upper bound on embed() text payload (UTF-8 bytes).

SentenceTransformer MiniLM caps input at 256 tokens (~1 KB of English); 32 KB
is a generous ceiling that keeps a buggy caller from blocking the asyncio
event loop inside ``json.dumps`` / ``writer.write`` while still accommodating
any realistic lore fragment."""


class EmbedResponse(TypedDict):
    """Shape of a successful daemon embed reply.

    The daemon returns this dict verbatim at the ``result`` key of the
    JSON-RPC envelope. Keeping it typed (rather than ``dict[str, Any]``)
    lets mypy catch daemon-side schema drift at the client call site.
    """

    embedding: list[float]
    model: str
    latency_ms: int


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

    async def embed(self, text: str) -> EmbedResponse:
        """Send an embed request and return the daemon's reply dict.

        Each call opens its own socket connection, so callers can await
        ``embed()`` concurrently with ``render()`` without client-side
        serialization. Daemon-side scheduling of embed vs. render is
        daemon-internal (see sidequest-daemon, story 37-23) — the client
        does not model or guarantee that concurrency.

        :raises ValueError: ``text`` exceeds ``MAX_EMBED_BYTES`` (UTF-8).
            The empty-text case is defended upstream —
            :class:`LoreFragment` enforces ``content`` min_length=1 at
            construction, and :func:`retrieve_lore_context` short-circuits
            on an empty query — so this client layer does not re-check.
        :raises DaemonUnavailableError: socket missing, connection refused,
            or response timed out.
        :raises DaemonRequestError: daemon returned ``{"error": {...}}``
            (e.g. ``EMBED_FAILED``), or the daemon returned a structurally
            invalid reply (missing / wrong-typed fields) — the latter
            surfaces as ``INVALID_RESPONSE`` so the worker's retry budget
            applies and the GM panel records a terminal outcome.
        """
        if len(text.encode("utf-8")) > MAX_EMBED_BYTES:
            raise ValueError(
                f"embed() text exceeds {MAX_EMBED_BYTES}-byte UTF-8 limit"
            )
        result = await self._call("embed", {"text": text})
        # Runtime validation — EmbedResponse is a TypedDict, not a
        # pydantic model, so mypy-only shape checks don't catch a daemon
        # that sends ``{"embedding": null}`` (partial flush mid-crash),
        # a non-list embedding (schema drift), or a missing key (hot
        # reload). Surface the class of failure as DaemonRequestError so
        # the worker's retry budget applies and the GM panel sees a
        # terminal ``INVALID_RESPONSE`` outcome rather than an unlabelled
        # partial span from a bubbled-up KeyError/TypeError.
        try:
            embedding = result["embedding"]
            model = result["model"]
            latency_ms = result["latency_ms"]
        except KeyError as exc:
            raise DaemonRequestError(
                "INVALID_RESPONSE", f"embed reply missing key {exc}"
            ) from exc
        if not isinstance(embedding, list):
            raise DaemonRequestError(
                "INVALID_RESPONSE",
                "embed reply 'embedding' is not a list",
            )
        if not embedding:
            # Zero-length embedding would propagate to
            # LoreStore.requeue_dimension_mismatched(0) and wipe every
            # stored vector. Refuse at the boundary.
            raise DaemonRequestError(
                "INVALID_RESPONSE",
                "embed reply 'embedding' is zero-length",
            )
        # ``bool`` is a subclass of ``int`` in Python; exclude it so a
        # daemon returning ``[True, False]`` does not silently pass as
        # a valid embedding of 1.0 / 0.0 floats.
        if not all(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            for v in embedding
        ):
            raise DaemonRequestError(
                "INVALID_RESPONSE",
                "embed reply 'embedding' contains non-numeric values",
            )
        if (
            not isinstance(model, str)
            or not isinstance(latency_ms, int)
            or isinstance(latency_ms, bool)
        ):
            raise DaemonRequestError(
                "INVALID_RESPONSE",
                "embed reply 'model'/'latency_ms' have wrong types",
            )
        return EmbedResponse(
            embedding=embedding,
            model=model,
            latency_ms=latency_ms,
        )

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = uuid.uuid4().hex[:12]
        with tracer.start_as_current_span("daemon_client.request") as span:
            span.set_attribute("daemon.method", method)
            span.set_attribute("daemon.request_id", request_id)
            if "tier" in params:
                span.set_attribute("daemon.tier", str(params["tier"]))
            if method == "embed":
                span.set_attribute(
                    "daemon.text_len", len(str(params.get("text", "")))
                )
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
            except TimeoutError as exc:
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
                except TimeoutError as exc:
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
                with contextlib.suppress(Exception):
                    await writer.wait_closed()


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
