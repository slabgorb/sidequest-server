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


def test_create_app_discovers_render_root_via_daemon_handshake(
    monkeypatch,
    tmp_path,
):
    """Regression: when SIDEQUEST_OUTPUT_DIR isn't set, create_app() must
    fall back to ~/.sidequest/daemon-output-dir (the handshake file the
    daemon writes at startup). Without this discovery the dev-default
    flow leaves /renders unmounted and every image 404s in the UI
    (playtest 2026-04-25 [P1] regression).
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    handshake_dir = fake_home / ".sidequest"
    handshake_dir.mkdir()
    daemon_output = tmp_path / "daemon-tmp" / "zimage"
    daemon_output.mkdir(parents=True)
    (handshake_dir / "daemon-output-dir").write_text(f"{daemon_output}\n")

    monkeypatch.delenv("SIDEQUEST_OUTPUT_DIR", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

    create_app()

    # create_app must have promoted the handshake value into the env so
    # downstream `_render_url_from_path` calls see the same root.
    import os as _os

    assert _os.environ.get("SIDEQUEST_OUTPUT_DIR") == str(daemon_output)


def test_render_url_from_path_publishes_image_unavailable_on_fallthrough(
    monkeypatch,
):
    """Regression: when the render path can't be rewritten (env unset, or
    path lives outside SIDEQUEST_OUTPUT_DIR), `_render_url_from_path`
    must emit an `image_unavailable` watcher event so the GM panel
    surfaces the silent fallthrough. CLAUDE.md OTEL principle.
    """
    monkeypatch.delenv("SIDEQUEST_OUTPUT_DIR", raising=False)

    captured: list[tuple[str, dict, dict]] = []

    def fake_publish(event_type, fields, *, component="", severity="info"):
        captured.append((event_type, fields, {"component": component, "severity": severity}))

    import sidequest.telemetry.watcher_hub as _hub

    monkeypatch.setattr(_hub, "publish_event", fake_publish)

    from sidequest.server.session_helpers import _render_url_from_path

    result = _render_url_from_path("/var/folders/h0/sq-daemon-xyz/zimage/render_abc.png")

    # Path returned verbatim (no rewrite available)
    assert result == "/var/folders/h0/sq-daemon-xyz/zimage/render_abc.png"
    # And the lie detector lit up
    unavailable = [(fields, meta) for et, fields, meta in captured if et == "image_unavailable"]
    assert unavailable, "expected image_unavailable watcher event on env-unset fallthrough"
    fields, meta = unavailable[0]
    assert fields["reason"] == "output_dir_unset"
    assert meta["component"] == "render"
    assert meta["severity"] == "warning"


def test_validator_starts_with_app() -> None:
    """create_app() registers a startup hook that boots the validator."""
    from fastapi.testclient import TestClient

    from sidequest.server.app import create_app

    app = create_app()
    with TestClient(app):
        validator = getattr(app.state, "validator", None)
        assert validator is not None, "app.state.validator should be populated at startup"
        assert validator.is_running()
    # On exit, the TestClient's shutdown lifespan triggers shutdown.
    assert not validator.is_running()
