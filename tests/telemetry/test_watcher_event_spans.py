"""Tests for the publish_event → OTEL span bridge.

When ``SIDEQUEST_WATCHER_AS_SPANS=1`` every ``publish_event`` call mints
a synthetic OTEL span so OTLP exporters (Jaeger) see semantic events.
The dashboard must continue to receive exactly one event per call —
``WatcherSpanProcessor`` skips the synthetic span to prevent double
publishing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.server.watcher import WatcherSpanProcessor
from sidequest.telemetry import watcher_hub
from sidequest.telemetry.watcher_hub import WATCHER_SYNTHETIC_ATTR


@pytest.fixture
def in_memory_exporter() -> InMemorySpanExporter:
    """Attach a fresh in-memory exporter to whatever global TracerProvider
    is already installed.

    OTEL refuses to override an existing global provider (logs a warning
    and keeps the old one), so we cooperate with whatever is there: if
    the proxy is still in place we install a real SDK provider; otherwise
    we add another span processor. Each test gets its own exporter
    instance, so cross-test span pollution is harmless — assertions only
    inspect the local exporter.
    """
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def test_no_span_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch, in_memory_exporter: InMemorySpanExporter
) -> None:
    monkeypatch.setattr(watcher_hub, "_WATCHER_AS_SPANS_ENABLED", False)
    watcher_hub.publish_event("turn_complete", {"turn": 1})
    assert in_memory_exporter.get_finished_spans() == ()


def test_synthetic_span_carries_event_fields_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch, in_memory_exporter: InMemorySpanExporter
) -> None:
    monkeypatch.setattr(watcher_hub, "_WATCHER_AS_SPANS_ENABLED", True)

    watcher_hub.publish_event(
        "turn_complete",
        {"turn": 1, "agent": "narrator", "tokens": 4096},
        component="orchestrator",
        severity="info",
    )

    spans = [
        s for s in in_memory_exporter.get_finished_spans()
        if s.name == "watcher.turn_complete"
    ]
    assert len(spans) == 1
    span = spans[0]
    assert span.attributes is not None
    assert span.attributes[WATCHER_SYNTHETIC_ATTR] == "1"
    assert span.attributes["watcher.event_type"] == "turn_complete"
    assert span.attributes["watcher.component"] == "orchestrator"
    assert span.attributes["watcher.severity"] == "info"
    assert span.attributes["field.turn"] == 1
    assert span.attributes["field.agent"] == "narrator"
    assert span.attributes["field.tokens"] == 4096


def test_non_primitive_field_values_are_json_stringified(
    monkeypatch: pytest.MonkeyPatch, in_memory_exporter: InMemorySpanExporter
) -> None:
    monkeypatch.setattr(watcher_hub, "_WATCHER_AS_SPANS_ENABLED", True)
    watcher_hub.publish_event(
        "state_transition",
        {"patch": {"path": "/hp", "op": "set", "value": 7}, "tags": ["combat", "boss"]},
    )
    spans = [
        s for s in in_memory_exporter.get_finished_spans()
        if s.name == "watcher.state_transition"
    ]
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs is not None
    assert attrs["field.patch"] == '{"path":"/hp","op":"set","value":7}'
    # Lists of primitives pass through as homogeneous sequences (OTEL accepts those).
    assert list(attrs["field.tags"]) == ["combat", "boss"]


def _fake_readable_span(name: str, attrs: dict[str, Any]) -> Any:
    """Stand-in for a ReadableSpan. Only the fields WatcherSpanProcessor
    inspects need to exist."""
    span = MagicMock()
    span.name = name
    span.attributes = attrs
    span.start_time = 0
    span.end_time = 1_000_000
    status = MagicMock()
    status.status_code.name = "OK"
    span.status = status
    return span


def test_watcher_processor_skips_synthetic_spans() -> None:
    """The processor must early-return on synthetic spans so the
    dashboard never sees a duplicate ``agent_span_close`` for an event
    that was already published directly through the hub."""
    hub = MagicMock()
    processor = WatcherSpanProcessor(hub)

    synthetic_span = _fake_readable_span(
        "watcher.turn_complete", {WATCHER_SYNTHETIC_ATTR: "1", "watcher.event_type": "turn_complete"}
    )
    processor.on_end(synthetic_span)

    hub.publish.assert_not_called()


def test_watcher_processor_still_publishes_real_spans() -> None:
    """Sanity check — the skip is narrow. Spans without the synthetic
    marker continue to flow to the dashboard as before. Uses a span
    name not in SPAN_ROUTES so we get exactly one (firehose) publish."""
    hub = MagicMock()
    processor = WatcherSpanProcessor(hub)

    real_span = _fake_readable_span(
        "test.unrouted_span", {"some_attr": "value"}
    )
    processor.on_end(real_span)

    hub.publish.assert_called_once()
    event = hub.publish.call_args[0][0]
    assert event["event_type"] == "agent_span_close"
    assert event["fields"]["name"] == "test.unrouted_span"
