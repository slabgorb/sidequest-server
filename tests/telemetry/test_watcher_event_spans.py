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
    monkeypatch.delenv("SIDEQUEST_WATCHER_AS_SPANS", raising=False)
    watcher_hub.publish_event("turn_complete", {"turn": 1})
    assert in_memory_exporter.get_finished_spans() == ()


def test_synthetic_span_carries_event_fields_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch, in_memory_exporter: InMemorySpanExporter
) -> None:
    monkeypatch.setenv("SIDEQUEST_WATCHER_AS_SPANS", "1")

    watcher_hub.publish_event(
        "turn_complete",
        {"turn": 1, "agent": "narrator", "tokens": 4096},
        component="orchestrator",
        severity="info",
    )

    spans = [
        s for s in in_memory_exporter.get_finished_spans() if s.name == "watcher.turn_complete"
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
    monkeypatch.setenv("SIDEQUEST_WATCHER_AS_SPANS", "1")
    watcher_hub.publish_event(
        "state_transition",
        {"patch": {"path": "/hp", "op": "set", "value": 7}, "tags": ["combat", "boss"]},
    )
    spans = [
        s for s in in_memory_exporter.get_finished_spans() if s.name == "watcher.state_transition"
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
        "watcher.turn_complete",
        {WATCHER_SYNTHETIC_ATTR: "1", "watcher.event_type": "turn_complete"},
    )
    processor.on_end(synthetic_span)

    hub.publish.assert_not_called()


def test_stats_surfaces_synthetic_span_counter_and_flag(
    monkeypatch: pytest.MonkeyPatch, in_memory_exporter: InMemorySpanExporter
) -> None:
    """The hub's ``stats()`` exposes the bridge state so probes can
    confirm the bridge is firing during gameplay without grepping logs."""
    monkeypatch.setenv("SIDEQUEST_WATCHER_AS_SPANS", "1")
    before = watcher_hub.watcher_hub.stats()["synthetic_spans"]

    watcher_hub.publish_event("turn_complete", {"turn": 99})
    watcher_hub.publish_event("turn_complete", {"turn": 100})

    stats = watcher_hub.watcher_hub.stats()
    assert stats["synthetic_spans"] == before + 2
    assert stats["watcher_as_spans"] == 1


def test_watcher_processor_still_publishes_real_spans() -> None:
    """Sanity check — the skip is narrow. Spans without the synthetic
    marker continue to flow to the dashboard as before. Uses a span
    name not in SPAN_ROUTES so we get exactly one (firehose) publish."""
    hub = MagicMock()
    processor = WatcherSpanProcessor(hub)

    real_span = _fake_readable_span("test.unrouted_span", {"some_attr": "value"})
    processor.on_end(real_span)

    hub.publish.assert_called_once()
    event = hub.publish.call_args[0][0]
    assert event["event_type"] == "agent_span_close"
    assert event["fields"]["name"] == "test.unrouted_span"


def test_synthetic_spans_count_increments_per_publish_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch, in_memory_exporter: InMemorySpanExporter
) -> None:
    """``synthetic_spans_count()`` is the per-turn diagnostic the
    websocket dispatch handler reads at turn entry/exit. A non-zero
    delta proves the bridge fired during the turn — closing the
    "is the bridge live during gameplay?" question that resume-only
    Jaeger output kept open."""
    from sidequest.telemetry.watcher_hub import synthetic_spans_count

    monkeypatch.setenv("SIDEQUEST_WATCHER_AS_SPANS", "1")
    before = synthetic_spans_count()

    watcher_hub.publish_event("turn_complete", {"turn": 1})
    watcher_hub.publish_event("state_transition", {"field": "location"})
    watcher_hub.publish_event("game_state_snapshot", {"reason": "turn"})

    assert synthetic_spans_count() - before == 3


def test_synthetic_spans_count_does_not_increment_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch, in_memory_exporter: InMemorySpanExporter
) -> None:
    """Counter must reflect actual mint state — a 0 delta in the
    server log unambiguously means "bridge flag was off this turn",
    not "bridge silently failed"."""
    from sidequest.telemetry.watcher_hub import synthetic_spans_count

    monkeypatch.delenv("SIDEQUEST_WATCHER_AS_SPANS", raising=False)
    before = synthetic_spans_count()

    watcher_hub.publish_event("turn_complete", {"turn": 1})

    assert synthetic_spans_count() - before == 0
