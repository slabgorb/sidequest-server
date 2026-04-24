"""FastAPI application entry point for sidequest-server.

Phase 1: /health + /ws WebSocket endpoint + REST endpoints.
Dependency-injected: ClaudeClient factory, genre pack search paths, save dir
are all configurable for tests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from sidequest.agents.claude_client import LlmClient
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.server.rest import create_rest_router
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.session_room import RoomRegistry
from sidequest.server.watcher import (
    WatcherSpanProcessor,
    watcher_endpoint,
    watcher_hub,
)
from sidequest.server.websocket import ws_endpoint

logger = logging.getLogger(__name__)


def _install_uvicorn_log_bridge() -> None:
    """Attach a StreamHandler to the ``sidequest`` logger tree so INFO
    lines surface in the uvicorn-driven log.

    Uvicorn installs its own dictConfig at serve time with
    ``disable_existing_loggers=True``, which disables any logger that
    isn't explicitly in its config. Attaching a handler directly on the
    ``sidequest`` tree keeps that tree alive through uvicorn's config
    swap without touching ``propagate`` — which would silently drop
    records from every caplog-based test later in the run.

    The previous implementation ran at module-import and set
    ``propagate=False``, which poisoned the logger tree for any test
    that later imported this module. The setup now runs only inside
    :func:`create_app`, and propagation stays on so tests using
    ``caplog`` continue to capture records off the root logger.
    """
    sq = logging.getLogger("sidequest")
    sq.setLevel(logging.INFO)
    if not any(getattr(h, "_sidequest_bridge", False) for h in sq.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s [%(name)s] %(message)s")
        )
        handler._sidequest_bridge = True  # type: ignore[attr-defined]
        sq.addHandler(handler)


def create_app(
    claude_client_factory: Callable[[], LlmClient] | None = None,
    genre_pack_search_paths: list[Path] | None = None,
    save_dir: Path | None = None,
) -> FastAPI:
    """Construct the FastAPI application.

    Args:
        claude_client_factory: Factory that returns a LlmClient client.
            Defaults to ``lambda: ClaudeClient()``.
        genre_pack_search_paths: Ordered list of directories to search for
            genre packs. Defaults to DEFAULT_GENRE_PACK_SEARCH_PATHS.
        save_dir: Root directory for SQLite save files.
            Defaults to ``~/.sidequest/saves``.
    """
    from sidequest.agents.claude_client import ClaudeClient

    resolved_save_dir: Path = save_dir or (
        Path.home() / ".sidequest" / "saves"
    )
    resolved_search_paths: list[Path] = (
        genre_pack_search_paths
        if genre_pack_search_paths is not None
        else DEFAULT_GENRE_PACK_SEARCH_PATHS
    )
    resolved_client_factory: Callable[[], LlmClient] = (
        claude_client_factory if claude_client_factory is not None
        else ClaudeClient
    )

    _install_uvicorn_log_bridge()

    app = FastAPI(
        title="sidequest-server",
        description="SideQuest Python API server (ADR-082 port target)",
        version="0.1.0",
    )

    # CORS — dev UI runs on Vite (5173) and fetches API routes cross-origin.
    # Without this, dashboard polls hit `/api/debug/state` and friends from
    # 5173 → 8765 and the browser blocks with "No Access-Control-Allow-Origin",
    # flooding the console. Localhost-only by default; keeps prod safe because
    # the server isn't exposed beyond localhost anyway.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store DI config on app.state so REST handlers can access it via Request
    app.state.claude_client_factory = resolved_client_factory
    app.state.genre_pack_search_paths = resolved_search_paths
    app.state.save_dir = resolved_save_dir
    app.state.room_registry = RoomRegistry()

    # --- Watcher hub — OTEL span broadcast for the GM dashboard. ---
    # The hub is a module-level singleton in `sidequest.server.watcher` so
    # subsystem code (session_handler, orchestrator) can publish semantic
    # events without threading a reference through every constructor.
    app.state.watcher_hub = watcher_hub

    @app.on_event("startup")
    async def _wire_watcher() -> None:
        import asyncio

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        from sidequest.telemetry.setup import init_tracer

        watcher_hub.bind_loop(asyncio.get_running_loop())

        # Ensure the global tracer provider is a real SDK TracerProvider
        # (not the default proxy) so add_span_processor is available.
        init_tracer()

        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            logger.warning(
                "watcher.span_processor_skipped reason=tracer_provider_is_%s",
                type(provider).__name__,
            )
            return

        # Idempotent registration. Under ``uvicorn --reload`` the startup
        # handler runs on every hot-reload, and the SDK's
        # ``add_span_processor`` happily stacks duplicates. Before this
        # guard, a 20-minute playtest session ended up with 30+
        # ``WatcherSpanProcessor`` instances, each pushing every span to
        # a (potentially stale) hub — the exact symptom on
        # 2026-04-23. We walk the provider's processor chain and skip
        # registration if one of ours is already wired up.
        existing = getattr(provider, "_active_span_processor", None)
        processors = getattr(existing, "_span_processors", ()) if existing else ()
        already_wired = any(
            isinstance(p, WatcherSpanProcessor) for p in processors
        )
        if already_wired:
            logger.info(
                "watcher.span_processor_already_registered count=%d",
                sum(1 for p in processors if isinstance(p, WatcherSpanProcessor)),
            )
            return

        provider.add_span_processor(WatcherSpanProcessor(watcher_hub))
        logger.info("watcher.span_processor_registered")

    # --- /health ---
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- /ws WebSocket endpoint ---
    @app.websocket("/ws")
    async def websocket_game(websocket: WebSocket) -> None:
        handler = WebSocketSessionHandler(
            claude_client_factory=app.state.claude_client_factory,
            genre_pack_search_paths=app.state.genre_pack_search_paths,
            save_dir=app.state.save_dir,
        )
        await ws_endpoint(websocket, handler)

    # --- /ws/watcher WebSocket endpoint — OTEL span stream to GM dashboard. ---
    @app.websocket("/ws/watcher")
    async def websocket_watcher(websocket: WebSocket) -> None:
        await watcher_endpoint(websocket, watcher_hub)

    # --- REST routes ---
    rest_router = create_rest_router()
    app.include_router(rest_router)

    # --- Static /genre/* mount — serve genre pack assets (POI images, portraits, etc.) ---
    # URL /genre/<genre>/worlds/<world>/assets/poi/<file> → first-matching genre_packs dir.
    # When no genre_packs dir exists (unit-test configurations that pass
    # a nonexistent path), skip the mount entirely — asset requests will
    # 404 loudly, which is correct signal, while the rest of the app
    # (including /api/genres returning {}) stays usable.
    genre_packs_dir: Path | None = next(
        (p for p in resolved_search_paths if p.exists() and p.is_dir()), None
    )
    if genre_packs_dir is not None:
        app.mount("/genre", StaticFiles(directory=str(genre_packs_dir)), name="genre_assets")
    else:
        logger.warning(
            "genre_assets.mount_skipped search_paths=%s",
            [str(p) for p in resolved_search_paths],
        )

    # --- Static /renders/* mount — serve daemon-generated images. ---
    # The daemon writes every render under SIDEQUEST_OUTPUT_DIR (same env
    # both processes read), and session_handler._render_url_from_path
    # maps those filesystem paths to /renders/<relative>. When the env
    # isn't set (unit tests, first-run) we skip the mount rather than
    # raise — renders are optional and the lobby stays usable.
    import os as _os

    render_root = _os.environ.get("SIDEQUEST_OUTPUT_DIR")
    if render_root:
        render_dir = Path(render_root)
        render_dir.mkdir(parents=True, exist_ok=True)
        app.mount(
            "/renders",
            StaticFiles(directory=str(render_dir)),
            name="render_assets",
        )
        logger.info("render_assets.mount_registered dir=%s", render_dir)
    else:
        logger.info(
            "render_assets.mount_skipped reason=SIDEQUEST_OUTPUT_DIR_unset"
        )

    return app


def main() -> None:
    """Entry point for `sidequest-server` CLI script."""
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
