"""Smoke tests proving the Phase 0 skeleton is wired correctly."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sidequest.server.app import create_app
from sidequest.telemetry import init_tracer, tracer


def test_fastapi_app_constructs() -> None:
    """The FastAPI app can be built without error."""
    app = create_app()
    assert app.title == "sidequest-server"


def test_health_endpoint_returns_ok() -> None:
    """The /health endpoint responds 200 with {status: ok}."""
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_otel_tracer_initializes() -> None:
    """The OTEL tracer can be initialized idempotently."""
    init_tracer()
    init_tracer()  # idempotent
    t = tracer()
    with t.start_as_current_span("smoke-span") as span:
        assert span is not None
