"""sidequest.daemon_client — async JSON-RPC client for the media daemon.

See :mod:`sidequest.daemon_client.client` for the implementation.
"""

from __future__ import annotations

from sidequest.daemon_client.client import (
    DEFAULT_SOCKET_PATH,
    MAX_EMBED_BYTES,
    DaemonClient,
    DaemonClientError,
    DaemonRequestError,
    DaemonUnavailableError,
    EmbedResponse,
    render_enabled,
)

__all__ = [
    "DEFAULT_SOCKET_PATH",
    "MAX_EMBED_BYTES",
    "DaemonClient",
    "DaemonClientError",
    "DaemonRequestError",
    "DaemonUnavailableError",
    "EmbedResponse",
    "render_enabled",
]
