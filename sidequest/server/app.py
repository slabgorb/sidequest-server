"""FastAPI application entry point for sidequest-server."""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Construct the FastAPI application.

    Phase 0: empty skeleton. Phase 1 wires /ws WebSocket endpoint via
    sidequest.server.websocket.register(app).
    """
    app = FastAPI(
        title="sidequest-server",
        description="SideQuest Python API server (ADR-082 port target)",
        version="0.1.0",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    """Entry point for `sidequest-server` CLI script."""
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")


if __name__ == "__main__":
    main()
