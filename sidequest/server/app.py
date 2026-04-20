"""FastAPI application entry point for sidequest-server.

Phase 1: /health + /ws WebSocket endpoint + REST endpoints.
Dependency-injected: ClaudeClient factory, genre pack search paths, save dir
are all configurable for tests.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles

from sidequest.agents.claude_client import ClaudeLike
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.server.rest import create_rest_router
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.websocket import ws_endpoint

logger = logging.getLogger(__name__)

# Surface INFO from sidequest.* loggers in the uvicorn-driven log. Uvicorn
# installs its own dictConfig which disables un-attached loggers, so
# logging.basicConfig() is a no-op here — we must attach a handler directly
# to the sidequest logger tree and disable propagation to root.
_sq_logger = logging.getLogger("sidequest")
_sq_logger.setLevel(logging.INFO)
if not _sq_logger.handlers:
    _sq_handler = logging.StreamHandler()
    _sq_handler.setFormatter(
        logging.Formatter("%(levelname)s [%(name)s] %(message)s")
    )
    _sq_logger.addHandler(_sq_handler)
    _sq_logger.propagate = False


def create_app(
    claude_client_factory: Callable[[], ClaudeLike] | None = None,
    genre_pack_search_paths: list[Path] | None = None,
    save_dir: Path | None = None,
) -> FastAPI:
    """Construct the FastAPI application.

    Args:
        claude_client_factory: Factory that returns a ClaudeLike client.
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
    resolved_client_factory: Callable[[], ClaudeLike] = (
        claude_client_factory if claude_client_factory is not None
        else ClaudeClient
    )

    app = FastAPI(
        title="sidequest-server",
        description="SideQuest Python API server (ADR-082 port target)",
        version="0.1.0",
    )

    # Store DI config on app.state so REST handlers can access it via Request
    app.state.claude_client_factory = resolved_client_factory
    app.state.genre_pack_search_paths = resolved_search_paths
    app.state.save_dir = resolved_save_dir

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

    # --- REST routes ---
    rest_router = create_rest_router()
    app.include_router(rest_router)

    # --- Static /genre/* mount — serve genre pack assets (POI images, portraits, etc.) ---
    # URL /genre/<genre>/worlds/<world>/assets/poi/<file> → first-matching genre_packs dir.
    # Fails loud if no genre_packs dir is found.
    genre_packs_dir: Path | None = next(
        (p for p in resolved_search_paths if p.exists() and p.is_dir()), None
    )
    if genre_packs_dir is None:
        raise RuntimeError(
            f"No genre_packs directory found in {[str(p) for p in resolved_search_paths]}. "
            f"Cannot serve /genre/* static assets."
        )
    app.mount("/genre", StaticFiles(directory=str(genre_packs_dir)), name="genre_assets")

    return app


def main() -> None:
    """Entry point for `sidequest-server` CLI script."""
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")


if __name__ == "__main__":
    main()
