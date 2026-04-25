"""Tests that ConsoleSpanExporter is gated behind SIDEQUEST_OTEL_CONSOLE."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter


def _has_console_exporter(provider: TracerProvider) -> bool:
    active = getattr(provider, "_active_span_processor", None)
    processors = getattr(active, "_span_processors", ()) if active else ()
    for proc in processors:
        exporter = getattr(proc, "span_exporter", None)
        if isinstance(exporter, ConsoleSpanExporter):
            return True
    return False


def _reset_tracer():
    """Force re-init by clearing the module-level _initialized flag."""
    from sidequest.telemetry import setup as setup_mod

    setup_mod._initialized = False
    # Reset the global provider to a fresh SDK provider so the next init wins.
    # OTEL uses a Once guard (_TRACER_PROVIDER_SET_ONCE) — we must reset both
    # the stored provider and the Once._done flag to allow re-setting.
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    once = getattr(trace, "_TRACER_PROVIDER_SET_ONCE", None)
    if once is not None:
        once._done = False


def test_console_exporter_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIDEQUEST_OTEL_CONSOLE", raising=False)
    _reset_tracer()

    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    assert not _has_console_exporter(provider), (
        "ConsoleSpanExporter should be off when SIDEQUEST_OTEL_CONSOLE is unset"
    )


def test_console_exporter_on_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIDEQUEST_OTEL_CONSOLE", "1")
    _reset_tracer()

    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    assert _has_console_exporter(provider), (
        "ConsoleSpanExporter should be enabled when SIDEQUEST_OTEL_CONSOLE=1"
    )
