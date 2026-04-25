"""Tests for ``create_app`` — confirms the default client factory honours
``SIDEQUEST_LLM_BACKEND`` end-to-end (ADR-073 Phase 2, Local DM Group E T10).

``create_app()`` stores the resolved factory on ``app.state.claude_client_factory``
(each ``/ws`` connection constructs a ``WebSocketSessionHandler`` with this
factory). When no explicit factory is injected, the default must be
``build_llm_client`` so the backend env var swaps Claude → Ollama on server
start without any code changes.
"""

from __future__ import annotations

from sidequest.server.app import create_app


def test_create_app_uses_build_llm_client_by_default(monkeypatch):
    monkeypatch.delenv("SIDEQUEST_LLM_BACKEND", raising=False)
    app = create_app()
    # The stored factory should return a ClaudeClient instance by default.
    from sidequest.agents.claude_client import ClaudeClient

    client = app.state.claude_client_factory()
    assert isinstance(client, ClaudeClient)


def test_create_app_honours_ollama_env(monkeypatch):
    monkeypatch.setenv("SIDEQUEST_LLM_BACKEND", "ollama")
    app = create_app()
    from sidequest.agents.ollama_client import OllamaClient

    client = app.state.claude_client_factory()
    assert isinstance(client, OllamaClient)


def test_validator_starts_with_app() -> None:
    """create_app() registers a startup hook that boots the validator."""
    from fastapi.testclient import TestClient

    from sidequest.server.app import create_app

    app = create_app()
    with TestClient(app):
        validator = getattr(app.state, "validator", None)
        assert validator is not None, (
            "app.state.validator should be populated at startup"
        )
        assert validator.is_running()
    # On exit, the TestClient's shutdown lifespan triggers shutdown.
    assert not validator.is_running()
