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
import contextlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult, VisualScene
from sidequest.protocol.enums import MessageType
from sidequest.server.session_handler import (
    WebSocketSessionHandler,
    _SessionData,
)


@pytest.fixture
def short_sock(tmp_path: Path) -> Path:
    """Short Unix-socket path (macOS caps sun_path ~104 bytes; pytest's
    tmp_path blows past it). Cleaned up after the test."""
    del tmp_path
    p = Path(f"/tmp/sq-render-test-{uuid.uuid4().hex[:8]}.sock")
    yield p
    if p.exists():
        p.unlink()


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
            with contextlib.suppress(Exception):
                await writer.wait_closed()

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


def _make_session_data_with_pc(
    player_name: str = "Rux",
    player_id: str = "p-1",
    *,
    race: str = "human",
    char_class: str = "ranger",
) -> _SessionData:
    """Variant of ``_make_session_data`` with a real Character on the snapshot
    so the portrait-dispatch path can project descriptor fields. The seat
    map is keyed by player_id → character.core.name so the resolver lands
    on this character (not the legacy first-PC path)."""
    from unittest.mock import MagicMock

    from sidequest.game.character import Character, CreatureCore
    from sidequest.game.session import GameSnapshot, TurnManager

    character = Character(
        core=CreatureCore(
            name=player_name,
            description="A weathered traveler.",
            personality="Quiet, observant.",
        ),
        backstory="Born in the dust under the lamppost towns.",
        char_class=char_class,
        race=race,
        pronouns="they/them",
    )
    snap = GameSnapshot(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        location="Tood's Dome — Nest Crack",
        turn_manager=TurnManager(interaction=3),
        characters=[character],
        player_seats={player_id: player_name},
    )
    sd = _SessionData(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        player_name=player_name,
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
        "sidequest.server.websocket_session_handler.DaemonClient",
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
    # Playtest 2026-04-30 fix: the server used to forward
    # ``sd.snapshot.location`` here as free-form narrator prose
    # ("Tood's Dome — Nest Crack"). The daemon's PromptComposer expects a
    # ``where:<scope>/<slug>`` PlaceCatalog ref; ``PlaceCatalog.get`` now
    # raises a ValueError on the scheme prefix check, which (pre-fix)
    # bubbled out of ``_handle_client`` and EOFed the socket. Until the
    # server tracks slug-aware locations, scene_illustration must send
    # ``location=""`` so the daemon's by-design "transient location" path
    # engages and the action prose carries the setting.
    assert req["params"]["location"] == "", (
        "scene_illustration must NOT forward free-form snapshot.location "
        "as a place ref — daemon's PlaceCatalog rejects non-`where:` refs"
    )
    assert req["params"]["genre"] == "mutant_wasteland"
    # Slice 1 of catalog-injected compose wiring + Bug #2a (playtest
    # 2026-04-26): server must send ``world`` so the daemon's compose path
    # can scope catalogs (CharacterCatalog, PlaceCatalog, StyleCatalog) per
    # (genre, world) AND engage the world-scoped visual_style. Without it,
    # the compose gate short-circuits and falls back to the legacy
    # prose-subject prompt — a silent styleless fallback.
    assert req["params"]["world"] == "flickering_reach", (
        "render request missing ``world`` — daemon's PromptComposer gate "
        "in sidequest_daemon/media/daemon.py will short-circuit and fall "
        "back to a styleless raw prompt"
    )


@pytest.mark.asyncio
async def test_render_dispatch_otel_includes_genre_and_world(
    tmp_path: Path, short_sock: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #2a lie-detector (CLAUDE.md OTEL Observability Principle).

    The GM panel needs to verify that the daemon's PromptComposer will
    actually engage. The dispatched watcher event must carry both
    ``genre`` and ``world`` so the panel can spot a styleless fallback
    before the rendered image arrives.
    """
    import asyncio as _asyncio

    from sidequest.telemetry.watcher_hub import watcher_hub

    sock = short_sock
    daemon = _FakeDaemon(
        reply_payload={
            "image_url": str(tmp_path / "render_x.png"),
            "width": 1024, "height": 768, "elapsed_ms": 50,
        }
    )
    await daemon.start(sock)

    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
    monkeypatch.setenv("SIDEQUEST_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "sidequest.server.websocket_session_handler.DaemonClient",
        lambda: _client_bound_to(sock),
    )

    # Bind the watcher hub to this loop and capture events.
    watcher_hub.bind_loop(_asyncio.get_running_loop())
    async with watcher_hub._lock:  # noqa: SLF001
        watcher_hub._subscribers.clear()  # noqa: SLF001

    class _Cap:
        def __init__(self) -> None:
            self.events: list[dict] = []

        async def send_json(self, data: dict) -> None:
            self.events.append(data)

    cap = _Cap()
    await watcher_hub.subscribe(cap)  # type: ignore[arg-type]

    handler, _queue = _make_handler_with_queue()
    sd = _make_session_data()
    result = NarrationTurnResult(
        narration="...",
        visual_scene=VisualScene(subject="x", tier="scene_illustration"),
    )
    handler._maybe_dispatch_render(sd, result)  # noqa: SLF001

    await _asyncio.sleep(0.1)
    await daemon.stop()

    dispatched = [
        e for e in cap.events
        if e.get("event_type") == "state_transition"
        and e.get("fields", {}).get("field") == "render"
        and e.get("fields", {}).get("op") == "dispatched"
    ]
    assert len(dispatched) == 1, (
        f"expected exactly 1 render.dispatched event, got {len(dispatched)}"
    )
    fields = dispatched[0]["fields"]
    assert fields.get("genre") == "mutant_wasteland", (
        "render.dispatched event missing ``genre`` — GM panel can't "
        "verify the daemon's PromptComposer will engage genre style"
    )
    assert fields.get("world") == "flickering_reach", (
        "render.dispatched event missing ``world`` — GM panel can't "
        "spot a styleless silent fallback before the image arrives"
    )


@pytest.mark.asyncio
async def test_portrait_dispatch_emits_structured_pc_ref_and_descriptor(
    tmp_path: Path, short_sock: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 2 of catalog-injected compose wiring (paired with sidequest-daemon
    PR registering runtime PCs from descriptor blobs).

    For portrait dispatches the server must emit a structured ``pc:<slug>``
    ref AND a descriptor blob so the daemon can ``add_pc`` into its
    CharacterCatalog without a disk-side portrait_manifest entry. Without
    this projection the daemon's compose path eats a CatalogMissError on
    every portrait and falls through to the prose-subject prompt — i.e.
    slice 2 is dead until both sides ship.

    Pins:
      * ``params["characters"] == ["pc:<slug>"]`` — the structured ref the
        daemon's ``build_render_target`` consumes for tier=PORTRAIT.
      * ``params["pc_descriptor"]`` carries id/appearance/default_pose/culture
        the daemon's ``CharacterTokens`` constructor needs.
      * Slug is lowercase, whitespace→``_``, drops punctuation — same rule
        as ``catalogs._slugify_name`` so PCs registered at runtime collide-
        check cleanly with NPCs loaded from disk.
    """
    sock = short_sock
    daemon = _FakeDaemon(
        reply_payload={
            "image_url": str(tmp_path / "portrait_xyz.png"),
            "width": 768,
            "height": 1024,
            "elapsed_ms": 3100,
        }
    )
    await daemon.start(sock)

    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
    monkeypatch.setenv("SIDEQUEST_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "sidequest.server.websocket_session_handler.DaemonClient",
        lambda: _client_bound_to(sock),
    )

    handler, queue = _make_handler_with_queue()
    sd = _make_session_data_with_pc(player_name="Roxie Two-Tongues")
    result = NarrationTurnResult(
        narration="Roxie's silhouette against the sodium lamps.",
        visual_scene=VisualScene(
            subject="a wiry rover lit by sodium lamps",
            tier="portrait",
            mood="moody",
            tags=["nightside"],
        ),
    )

    queued = handler._maybe_dispatch_render(sd, result)  # noqa: SLF001
    assert queued is not None

    # Drain the background render so the daemon receives the params.
    await asyncio.wait_for(queue.get(), timeout=2.0)
    await daemon.stop()

    assert len(daemon.requests) == 1
    params = daemon.requests[0]["params"]
    assert params["tier"] == "portrait"
    # Structured PC ref — slugified to mirror catalogs._slugify_name.
    assert params["characters"] == ["pc:roxie_two-tongues"]
    descriptor = params["pc_descriptor"]
    assert descriptor["id"] == "roxie_two-tongues"
    assert descriptor["appearance"], "appearance prose must be non-empty"
    # ``human ranger`` — descriptor's appearance prose is built from
    # (race, char_class) — that's what the daemon will replicate to every LOD.
    assert "human" in descriptor["appearance"].lower()
    assert "ranger" in descriptor["appearance"].lower()
    # Slug rule: lowercase + collapse whitespace + drop punctuation except _/-.
    # An apostrophe (e.g. "O'Connor") would be dropped, but here the test
    # name has only the hyphen which is preserved.
    assert "-" in descriptor["id"]


@pytest.mark.asyncio
async def test_scene_illustration_dispatch_uses_characters_key_not_participants(
    tmp_path: Path, short_sock: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Playtest 2026-04-30 contract drift: scene_illustration dispatch
    used to set ``params["participants"] = [pc:<slug>]`` but the daemon's
    ``build_cue_from_params`` only reads ``params.get("characters", [])``
    — so the PC ref never reached the composer's casting plan and every
    illustration rendered with no participant tokens. The portrait
    branch already used ``characters``; both branches must use the same
    on-the-wire field.
    """
    sock = short_sock
    daemon = _FakeDaemon(
        reply_payload={
            "image_url": str(tmp_path / "scene_xyz.png"),
            "width": 1024,
            "height": 768,
            "elapsed_ms": 4500,
        }
    )
    await daemon.start(sock)

    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
    monkeypatch.setenv("SIDEQUEST_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "sidequest.server.websocket_session_handler.DaemonClient",
        lambda: _client_bound_to(sock),
    )

    handler, queue = _make_handler_with_queue()
    sd = _make_session_data_with_pc(player_name="Hokulea")
    result = NarrationTurnResult(
        narration="Hokulea pries open the sprung locker.",
        visual_scene=VisualScene(
            subject="a sprung exploration locker in red corridor light",
            tier="scene_illustration",
            mood="urgent",
            tags=["ship-interior"],
        ),
    )

    queued = handler._maybe_dispatch_render(sd, result)  # noqa: SLF001
    assert queued is not None

    await asyncio.wait_for(queue.get(), timeout=2.0)
    await daemon.stop()

    assert len(daemon.requests) == 1
    params = daemon.requests[0]["params"]
    assert params["tier"] == "scene_illustration"
    assert params["characters"] == ["pc:hokulea"], (
        "scene_illustration must send PC ref under ``characters`` (the "
        "key the daemon reads), not ``participants``. Pre-fix, the daemon "
        "saw an empty participants list and the casting layer was empty."
    )
    assert "participants" not in params, (
        "legacy ``participants`` key must not be sent — daemon doesn't "
        "read it and leaving it in is a contract trap"
    )
    # And the location-fix from the same playtest: scene_illustration
    # must NOT forward the snapshot's free-form prose location.
    assert params["location"] == ""
    # Descriptor still flows through so the daemon can register the PC.
    assert params["pc_descriptor"]["id"] == "hokulea"


@pytest.mark.asyncio
async def test_portrait_dispatch_omits_descriptor_when_no_character_seated(
    tmp_path: Path, short_sock: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the snapshot has no character to project (e.g. an early portrait
    fired before chargen confirmation), the structured PC ref still ships —
    the daemon's safe wrapper will catalog-miss + fall back. The descriptor
    is omitted so we don't ship empty-prose tokens that would degrade the
    fallback prompt."""
    sock = short_sock
    daemon = _FakeDaemon(
        reply_payload={
            "image_url": str(tmp_path / "portrait_empty.png"),
            "width": 768,
            "height": 1024,
            "elapsed_ms": 3000,
        }
    )
    await daemon.start(sock)

    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")
    monkeypatch.setenv("SIDEQUEST_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "sidequest.server.websocket_session_handler.DaemonClient",
        lambda: _client_bound_to(sock),
    )

    handler, queue = _make_handler_with_queue()
    sd = _make_session_data(player_id="p-2")  # no characters on snapshot
    result = NarrationTurnResult(
        narration="...",
        visual_scene=VisualScene(subject="a face in shadow", tier="portrait"),
    )

    queued = handler._maybe_dispatch_render(sd, result)  # noqa: SLF001
    assert queued is not None
    await asyncio.wait_for(queue.get(), timeout=2.0)
    await daemon.stop()

    params = daemon.requests[0]["params"]
    # Ref still ships — daemon's try_compose handles the catalog miss.
    assert params["characters"] == ["pc:rux"]
    assert "pc_descriptor" not in params


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
        "sidequest.server.websocket_session_handler.DaemonClient",
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
            "sidequest.server.websocket_session_handler.DaemonClient",
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
