"""Process-wide pub/sub for WatcherEvent broadcasts.

Lives in `sidequest.telemetry` rather than `sidequest.server` so subsystem
code (orchestrator, game, genre) can publish semantic events without
pulling in FastAPI / uvicorn at import time. Importing
`sidequest.server.watcher` would trigger `sidequest.server.__init__`,
which imports `app.py`, which imports `uvicorn` — and uvicorn
reconfigures logging handlers at import, breaking pytest's caplog
fixture.

The FastAPI-facing pieces (WebSocket endpoint, OTEL SpanProcessor) live
in `sidequest.server.watcher` and import from here.

Reload safety: under ``uvicorn --reload`` the module is re-imported
whenever source files change. A naïve module-level ``watcher_hub =
WatcherHub()`` would create a fresh singleton on every reload, orphaning
any OTEL span processors registered against the previous instance and
turning the dashboard deaf once the first reload fires. We pin the hub
to a builtins attribute so the same instance survives re-imports of
this module within the same interpreter.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_BUILTINS_HUB_ATTR = "_sidequest_watcher_hub_singleton"

# When set, every ``publish_event`` call also opens-and-closes a tiny OTEL
# span so the OTLP exporter (e.g. local Jaeger) sees the semantic event
# stream — not just spans started via ``tracer().start_as_current_span``.
# Default off to keep test event counts stable; opt-in via the env var.
# ``WatcherSpanProcessor`` recognizes the synthetic marker attribute and
# skips re-publishing them as ``agent_span_close`` events, so the GM
# dashboard is unaffected.
_WATCHER_AS_SPANS_ENABLED = os.environ.get("SIDEQUEST_WATCHER_AS_SPANS") == "1"
WATCHER_SYNTHETIC_ATTR = "sidequest.watcher_synthetic"


class _Sendable(Protocol):
    """Structural stand-in for ``fastapi.WebSocket`` — anything with
    ``send_json``. Keeps this module free of fastapi."""

    async def send_json(self, data: dict[str, Any]) -> None: ...


class WatcherHub:
    """Thread-safe pub/sub for WatcherEvent broadcasts."""

    def __init__(self) -> None:
        self._subscribers: set[_Sendable] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Health counters so the hub can surface its own liveness via
        # `watcher.health` events (see :func:`publish_event`). A
        # self-observing observability layer is the point of the GM panel
        # — if the bus is silent, the operator needs to see WHY.
        self._published_count: int = 0
        self._dropped_count: int = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the FastAPI event loop so background-thread publishers
        can hop onto it. Called once during app startup."""
        self._loop = loop

    async def subscribe(self, ws: _Sendable) -> None:
        async with self._lock:
            self._subscribers.add(ws)
        logger.info("watcher.subscribed total=%d", len(self._subscribers))

    async def unsubscribe(self, ws: _Sendable) -> None:
        async with self._lock:
            self._subscribers.discard(ws)
        logger.info("watcher.unsubscribed total=%d", len(self._subscribers))

    def publish(self, event: dict[str, Any]) -> None:
        """Broadcast an event to all subscribers.

        Safe to call from any thread. If the event loop isn't bound yet
        (process start-up race), drop the event — the dashboard treats
        the stream as lossy by design.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            self._dropped_count += 1
            return
        self._published_count += 1
        asyncio.run_coroutine_threadsafe(self._broadcast(event), loop)

    def stats(self) -> dict[str, int]:
        """Snapshot of broadcast counters. Exposed so the GM dashboard
        can confirm the bus is alive without grepping the server log."""
        return {
            "subscribers": len(self._subscribers),
            "published": self._published_count,
            "dropped": self._dropped_count,
        }

    async def _broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._subscribers)
        if not targets:
            return
        # Pre-serialize once with a tolerant encoder to a JSON-safe dict.
        # This decouples encoding errors (one bad publisher) from
        # delivery errors (one dead subscriber). Without this, a Pydantic
        # ``NonBlankString`` (or any other non-stdlib JSON value) hidden
        # in an event raised ``TypeError`` inside Starlette's
        # ``send_json``; the per-subscriber ``except`` then treated every
        # live WebSocket as dead and evicted the GM dashboard.
        # (Playtest 2026-04-29.)
        try:
            safe_event = json.loads(
                json.dumps(event, default=_json_default, separators=(",", ":"))
            )
        except (TypeError, ValueError) as exc:
            # One bad event must not kill subscribers. Log loudly so the
            # offending publisher is fixable, then drop the event.
            logger.warning(
                "watcher_hub.serialize_failed event_type=%s err=%r — event dropped, subscribers preserved",
                event.get("event_type", "?"),
                exc,
            )
            self._dropped_count += 1
            return
        dead: list[_Sendable] = []
        for ws in targets:
            try:
                await ws.send_json(safe_event)
            except Exception:  # noqa: BLE001 — broadcast is best-effort
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._subscribers.discard(ws)
            logger.info(
                "watcher.subscribers_pruned count=%d remaining=%d",
                len(dead),
                len(self._subscribers),
            )


def _json_default(obj: Any) -> Any:
    """Tolerant JSON fallback for watcher events.

    Subsystem code publishes typed values that the standard ``json``
    module can't encode — most commonly Pydantic ``RootModel`` newtypes
    (``NonBlankString``, ``Stat``) and ``datetime``. Coercing to ``str``
    is the right call for the GM dashboard: the dashboard treats event
    values as opaque labels, and a string representation preserves
    every field that any subsystem cares to inspect.

    Falls through to ``TypeError`` for anything else so a genuinely bad
    event surfaces in the per-event ``serialize_failed`` warning rather
    than silently degrading.
    """
    # Pydantic RootModel — covers NonBlankString, Stat, and any future
    # transparent newtype.
    root = getattr(obj, "root", None)
    if root is not None and isinstance(root, (str, int, float, bool)):
        return root
    # ``datetime``/``date``/``UUID``: ``str()`` round-trips. Same for
    # ``Path`` and ``Decimal``.
    if isinstance(obj, (datetime, )):
        return obj.isoformat()
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# Module-level singleton. FastAPI runs one app per process, so a single
# hub is correct by construction. Subsystem code that needs to publish
# semantic events (`turn_complete`, `state_transition`, etc.) imports
# this and calls :func:`publish_event` — no dependency injection
# required. Safe to import at module load: `publish` is a no-op until
# :meth:`WatcherHub.bind_loop` runs during FastAPI startup.
#
# Pin the instance to ``builtins`` so ``uvicorn --reload`` — which
# re-imports changed modules — preserves the same hub across reloads.
# Without this, each reload installs a fresh hub, orphaning all OTEL
# span processors registered against the previous instance and turning
# the dashboard deaf. (Playtest 2026-04-23.)
#
# Identity check is by-name, not ``isinstance``: after ``importlib.reload``
# the new ``WatcherHub`` class is a fresh object, so an instance created
# before the reload fails ``isinstance`` against the post-reload class
# even though its interface is identical. ``type(x).__name__`` is stable.
_existing = getattr(builtins, _BUILTINS_HUB_ATTR, None)
if _existing is not None and type(_existing).__name__ == "WatcherHub":
    watcher_hub: WatcherHub = _existing  # type: ignore[assignment]
else:
    watcher_hub = WatcherHub()
    setattr(builtins, _BUILTINS_HUB_ATTR, watcher_hub)


# ---------------------------------------------------------------------------
# Process-wide event store binding — persists encounter state_transition events
# to the SQLite events table. Bound at session-handler startup; global by
# design (one session per process during playtest). See Task 20 doc comment.
# ---------------------------------------------------------------------------

_event_store = None  # bound at session-handler startup; weakref-safe by class id


def bind_event_store(store) -> None:
    """Bind a SqliteStore so encounter watcher events persist as rows.

    Multiple binds replace; ``None`` clears (used by tests).
    """
    global _event_store
    _event_store = store


_KIND_BY_OP: dict[str, str] = {
    "started": "ENCOUNTER_STARTED",
    "beat_applied": "ENCOUNTER_BEAT_APPLIED",
    "metric_advance": "ENCOUNTER_METRIC_ADVANCE",
    "beat_skipped": "ENCOUNTER_BEAT_SKIPPED",
    "tag_created": "ENCOUNTER_TAG_CREATED",
    "tag_backfire": "ENCOUNTER_TAG_CREATED",  # backfire is still a tag-creation row
    "status_added": "ENCOUNTER_STATUS_ADDED",
    "yield_received": "ENCOUNTER_YIELD",
    "yield_resolved": "ENCOUNTER_YIELD",
    "resolved": "ENCOUNTER_RESOLVED",
    # Reserved — no current callsite emits this op (would break ENCOUNTER_RESOLVED-last
    # ordering invariant). Future sites that emit signal-creation outside of resolution
    # may use it.
    "resolution_signal_emitted": "ENCOUNTER_RESOLUTION_SIGNAL",
    "resolution_signal_consumed": "ENCOUNTER_RESOLUTION_SIGNAL",
}


def _maybe_persist_encounter_row(event: dict) -> None:
    global _event_store
    if _event_store is None:
        return
    if event.get("event_type") != "state_transition":
        return
    fields = event.get("fields", {})
    if fields.get("field") != "encounter":
        return
    op = str(fields.get("op", ""))
    kind = _KIND_BY_OP.get(op)
    if kind is None:
        return
    payload = json.dumps(fields)
    try:
        _event_store._conn.execute(
            "INSERT INTO events (kind, payload_json, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (kind, payload),
        )
        _event_store._conn.commit()
    except sqlite3.ProgrammingError as exc:
        # The bound store's connection has been closed out from under us
        # (typical: session disconnect closed the store but never called
        # ``bind_event_store(None)``; or tests that close the store via
        # ``store.close()`` without unbinding first). Treat the binding
        # as stale and clear it so the next caller doesn't hit the same
        # dead handle. We log loudly (warning, not silent) so the GM
        # panel / OTEL trail records the recovery — silently swallowing
        # would mask a real lifecycle mismatch.
        logger.warning(
            "watcher_hub.event_store_closed — clearing stale binding "
            "(kind=%s op=%s err=%s)",
            kind,
            op,
            exc,
        )
        _event_store = None


def _coerce_attr_value(value: Any) -> Any:
    """Coerce a watcher field value to an OTEL-attribute-safe primitive.

    OTEL accepts ``str | bool | int | float`` and homogeneous sequences
    of those — those pass through so Jaeger renders them as native arrays.
    Anything else gets JSON-stringified using the same tolerant encoder
    the WebSocket broadcast uses, so Pydantic newtypes, datetimes, etc.
    round-trip the same way the dashboard sees them.
    """
    if isinstance(value, (str, bool, int, float)):
        return value
    if value is None:
        return ""
    if isinstance(value, (list, tuple)) and value:
        first_type = type(value[0])
        if first_type in (str, int, float) and all(
            type(x) is first_type for x in value
        ):
            return list(value)
    try:
        return json.dumps(value, default=_json_default, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _emit_watcher_span(
    event_type: str,
    fields: dict[str, Any],
    component: str,
    severity: str,
) -> None:
    """Mint a zero-duration OTEL span describing this watcher event.

    Attaches as a child of any active span, so traces in Jaeger group
    semantic events under the operation that triggered them.
    """
    # Local import keeps watcher_hub fastapi/uvicorn-free at module load
    # (the import-cycle reason this module exists separately from
    # ``sidequest.server.watcher`` — see module docstring).
    from opentelemetry import trace

    tracer = trace.get_tracer("sidequest-server.watcher")
    with tracer.start_as_current_span(f"watcher.{event_type}") as span:
        span.set_attribute(WATCHER_SYNTHETIC_ATTR, "1")
        span.set_attribute("watcher.event_type", event_type)
        span.set_attribute("watcher.component", component)
        span.set_attribute("watcher.severity", severity)
        for k, v in fields.items():
            span.set_attribute(f"field.{k}", _coerce_attr_value(v))


def publish_event(
    event_type: str,
    fields: dict[str, Any],
    *,
    component: str = "sidequest-server",
    severity: str = "info",
) -> None:
    """Publish a semantic WatcherEvent to the dashboard.

    Matches the TypeScript ``WatcherEvent`` shape (see
    ``sidequest-ui/src/types/watcher.ts``). Safe to call from any thread;
    drops silently if the hub has no bound event loop yet (process
    startup race) or no subscribers (dashboard closed).

    When ``SIDEQUEST_WATCHER_AS_SPANS=1`` also mints a synthetic OTEL
    span so OTLP exporters (Jaeger) can see semantic events alongside
    real spans. The dashboard is unaffected: ``WatcherSpanProcessor``
    skips synthetic spans rather than double-publishing them.

    :param event_type: One of the ``WatcherEventType`` union members
        (``turn_complete``, ``state_transition``, ``game_state_snapshot``,
        ``prompt_assembled``, ``lore_retrieval``, etc.).
    :param fields: Event-specific fields. Schema per event type is
        defined on the TypeScript side; keep keys stable.
    :param component: Subsystem label — drives the Subsystems tab's
        component grouping. Examples: ``orchestrator``, ``npc_registry``,
        ``state.location``, ``prompt_builder``, ``rag``.
    :param severity: ``info`` | ``warning`` | ``error``.
    """
    watcher_hub.publish(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": component,
            "event_type": event_type,
            "severity": severity,
            "fields": fields,
        }
    )
    _maybe_persist_encounter_row({"event_type": event_type, "fields": fields})
    if _WATCHER_AS_SPANS_ENABLED:
        _emit_watcher_span(event_type, fields, component, severity)
