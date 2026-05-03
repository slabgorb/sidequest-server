"""RED tests — Story 45-31 — wire-first unavailable fallback (AC4).

The Felix anti-silence test. Playtest 2026-04-19: 13 minutes elapsed
between the last successful render and session end with zero render
attempts surfacing in any save-file or OTEL signal. The bug post-mortem
revealed that ``_maybe_dispatch_render`` had no way to distinguish
"daemon socket missing" from "daemon hung mid-render" from "daemon
heartbeat stopped 8 minutes ago" — they all collapsed into the same
silent miss path.

This test pins the contract: when the ``DaemonStateMirror`` reports the
daemon UNRESPONSIVE (no heartbeat for >2× interval), the dispatcher
MUST:

  1. emit a ``render.unavailable`` watcher event with
     ``reason="heartbeat_lost"`` and a non-null ``last_heartbeat_ts``;
  2. NOT attempt the daemon round-trip (no socket connect, no
     pre-existing ``client.is_available()`` recovery);
  3. mark the scrapbook row with ``render_status="unavailable"``
     so the UI shows "Render unavailable" instead of a silent gap.

Wire-first: tests exercise ``handler._maybe_dispatch_render`` against a
real DaemonClient that points at a socket which intentionally never
emits a heartbeat. The boundary test crosses the dispatcher → mirror →
watcher hub → scrapbook emitter chain.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult, VisualScene
from sidequest.server.session_handler import (
    WebSocketSessionHandler,
    _SessionData,
)


@pytest.fixture
def short_sock(tmp_path: Path) -> Path:
    del tmp_path
    p = Path(f"/tmp/sq-unavail-test-{uuid.uuid4().hex[:8]}.sock")
    yield p
    if p.exists():
        p.unlink()


def _make_session_data() -> _SessionData:
    from unittest.mock import MagicMock

    from sidequest.game.session import GameSnapshot, TurnManager

    snap = GameSnapshot(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        location="",
        turn_manager=TurnManager(interaction=4),
    )
    return _SessionData(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        player_name="Rux",
        player_id="player-felix",
        snapshot=snap,
        store=MagicMock(),
        genre_pack=MagicMock(),
        orchestrator=MagicMock(),
    )


def _make_handler() -> WebSocketSessionHandler:
    handler = WebSocketSessionHandler(save_dir=Path("/tmp/never-used"))
    handler._out_queue = asyncio.Queue()  # noqa: SLF001 — test wiring
    return handler


def _make_visual_result() -> NarrationTurnResult:
    return NarrationTurnResult(
        narration="The crack yawns open. A pale glow pulses behind it.",
        visual_scene=VisualScene(
            subject="a jagged fissure in red rock",
            tier="scene_illustration",
            mood="ominous",
            tags=["desert", "ruin"],
        ),
    )


async def _capture_watcher_events() -> tuple[list[dict], object]:
    from sidequest.telemetry.watcher_hub import watcher_hub

    watcher_hub.bind_loop(asyncio.get_running_loop())
    async with watcher_hub._lock:  # noqa: SLF001
        watcher_hub._subscribers.clear()  # noqa: SLF001

    class _Cap:
        def __init__(self) -> None:
            self.events: list[dict] = []

        async def send_json(self, data: dict) -> None:
            self.events.append(data)

    cap = _Cap()
    await watcher_hub.subscribe(cap)  # type: ignore[arg-type]
    return cap.events, cap


class _NoOpDaemon:
    """Listens on a socket but never replies — tracks accept count so
    the test can assert no round-trip was attempted."""

    def __init__(self) -> None:
        self.accept_count = 0
        self._server: asyncio.AbstractServer | None = None

    async def start(self, path: Path) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=str(path))

    async def _handle(self, reader, writer) -> None:  # noqa: ANN001
        self.accept_count += 1
        try:
            # Drain whatever the client sent and hold the connection so
            # the test can detect that it was opened.
            await reader.readline()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


@pytest.mark.asyncio
async def test_unresponsive_daemon_emits_render_unavailable_event(
    short_sock: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: with the mirror in UNRESPONSIVE state, the dispatcher
    emits ``render.unavailable`` with ``reason="heartbeat_lost"`` and
    a non-null ``last_heartbeat_ts``."""
    daemon = _NoOpDaemon()
    await daemon.start(short_sock)
    try:
        monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")

        from sidequest.daemon_client import DaemonClient

        monkeypatch.setattr(
            "sidequest.server.websocket_session_handler.DaemonClient",
            lambda: DaemonClient(socket_path=short_sock, timeout_seconds=0.5),
        )

        # Seed the mirror with a heartbeat that's too old (> 2× interval).
        from sidequest.daemon_client.state_mirror import get_mirror

        mirror = get_mirror()
        mirror.clear_for_test()
        # Seed a recorded heartbeat then force UNRESPONSIVE so the
        # last_heartbeat_ts attribute on render.unavailable is non-null
        # while is_unresponsive() returns True deterministically.
        mirror.record_heartbeat(
            queue="image",
            state="ready",
            queue_depth=0,
            ts_monotonic=0.0,
        )
        mirror.force_unresponsive_for_test()

        captured, _cap = await _capture_watcher_events()

        handler = _make_handler()
        sd = _make_session_data()

        result = handler._maybe_dispatch_render(sd, _make_visual_result())  # noqa: SLF001

        # Allow watcher publish to flush.
        await asyncio.sleep(0.1)

        # AC4 (1): no daemon round-trip attempted. The fake daemon
        # tracks accept counts; an unresponsive-fallback dispatch must
        # not open a socket.
        assert daemon.accept_count == 0, (
            f"AC4 forbids the daemon round-trip when the mirror is "
            f"UNRESPONSIVE; got {daemon.accept_count} accepts"
        )

        # AC4 (2): render.unavailable event must fire with the
        # documented attributes.
        unavail = [
            e
            for e in captured
            if e.get("event_type") == "state_transition"
            and e.get("fields", {}).get("field") == "render"
            and e.get("fields", {}).get("op") == "unavailable"
        ]
        assert len(unavail) == 1, (
            f"expected exactly 1 render.unavailable event, got {len(unavail)}"
        )
        fields = unavail[0]["fields"]
        assert fields["reason"] == "heartbeat_lost"
        assert fields.get("last_heartbeat_ts") is not None, (
            "render.unavailable must carry the last seen heartbeat ts so "
            "post-mortem can quantify the silence window"
        )
        assert "turn_number" in fields
        assert "player_id" in fields

        # AC4: dispatcher returns None — no RENDER_QUEUED frame ships
        # because no render is actually in flight.
        assert result is None, (
            "unresponsive fallback must return None; no RENDER_QUEUED "
            "should ship for a render that never enters the daemon"
        )
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_unresponsive_fallback_marks_scrapbook_row_unavailable(
    short_sock: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: the scrapbook row persisted for the failing turn must
    carry ``render_status="unavailable"``. This is what the UI keys on
    to render the placeholder badge."""
    daemon = _NoOpDaemon()
    await daemon.start(short_sock)
    try:
        monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")

        from sidequest.daemon_client import DaemonClient

        monkeypatch.setattr(
            "sidequest.server.websocket_session_handler.DaemonClient",
            lambda: DaemonClient(socket_path=short_sock, timeout_seconds=0.5),
        )

        from sidequest.daemon_client.state_mirror import get_mirror

        mirror = get_mirror()
        mirror.record_heartbeat(
            queue="image", state="ready", queue_depth=0, ts_monotonic=0.0
        )
        mirror.force_unresponsive_for_test()

        # Capture scrapbook payloads written through the persistence
        # path. The dispatcher's fallback MUST emit a row with
        # render_status=unavailable, replacing the silent miss.
        from sidequest.protocol.messages import ScrapbookEntryPayload
        from sidequest.server import emitters as _emitters

        persisted: list[ScrapbookEntryPayload] = []

        def _spy_persist(handler, payload):  # noqa: ANN001
            persisted.append(payload)

        monkeypatch.setattr(_emitters, "persist_scrapbook_entry", _spy_persist)

        handler = _make_handler()
        sd = _make_session_data()
        handler._maybe_dispatch_render(sd, _make_visual_result())  # noqa: SLF001

        await asyncio.sleep(0.1)

        # AC4 (3): exactly one scrapbook row, marked unavailable.
        assert len(persisted) == 1, (
            f"expected exactly 1 scrapbook row from the fallback; got "
            f"{len(persisted)}"
        )
        row = persisted[0]
        # The new field must exist on the schema and be set to
        # 'unavailable' on the fallback path.
        assert getattr(row, "render_status", None) == "unavailable", (
            "AC4 requires the scrapbook row to carry "
            "render_status='unavailable' so the UI shows the placeholder "
            "badge instead of a silent gap"
        )
    finally:
        await daemon.stop()


def test_scrapbook_payload_schema_has_render_status_field() -> None:
    """The protocol schema must expose ``render_status`` with the unified
    enum spanning Story 45-30 (trigger-policy outcome) and Story 45-31
    (daemon-liveness outcome): ``rendered`` | ``skipped_policy`` |
    ``failed`` | ``unavailable``.

    Per 45-31 context: "If 45-30 lands first, this story extends the
    enum with 'unavailable'." 45-30 landed first.

    Wiring guard — the daemon-side, the UI, and the persistence layer
    all key on this field name. If the schema rejects the value, the
    fallback path crashes before the watcher event lands and the GM
    panel sees nothing."""
    from sidequest.protocol.messages import ScrapbookEntryPayload

    payload = ScrapbookEntryPayload(
        turn_id=1,
        location="Tood's Dome",
        narrative_excerpt="...",
        render_status="unavailable",
    )
    assert payload.render_status == "unavailable"

    for value in ("rendered", "skipped_policy", "failed"):
        payload = ScrapbookEntryPayload(
            turn_id=1,
            location="Tood's Dome",
            narrative_excerpt="...",
            render_status=value,
        )
        assert payload.render_status == value

    # Default when omitted — per the unified enum's default, "rendered"
    # (matches the develop behavior 45-30 shipped; existing rows that
    # pre-date the discriminator continue to read as the happy path).
    payload = ScrapbookEntryPayload(
        turn_id=1,
        location="Tood's Dome",
        narrative_excerpt="...",
    )
    assert payload.render_status == "rendered"
