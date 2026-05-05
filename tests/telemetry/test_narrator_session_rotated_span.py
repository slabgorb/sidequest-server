"""Story 45-47 — ADR-066 §10 narrator.session_rotated span helper.

The span helper lives alongside ``narrator.sealed_round`` in
``sidequest/telemetry/spans/narrator.py`` and is emitted on every
session rotation (proactive watchdog or reactive recovery).

These tests pin the contract:

* Constant name ``SPAN_NARRATOR_SESSION_ROTATED == "narrator.session_rotated"``
* Helper context manager ``narrator_session_rotated_span(...)``
* Required attributes per §10:
  - ``reason`` (one of: cli_error, session_expired, token_threshold, unknown)
  - ``cumulative_tokens`` (int)
  - ``turn_number`` (int)
  - ``recap_chars`` (int)
  - ``rebuild_latency_ms`` (int)
* Optional attributes (only set when applicable):
  - ``threshold`` (int) — only when reason == token_threshold
  - ``cli_error_signature`` (str) — only when reason == cli_error / session_expired
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


def _fresh_provider():
    """Build a fresh TracerProvider + in-memory exporter pair."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _local_tracer(provider: TracerProvider):
    return provider.get_tracer("test.narrator_session_rotated")


def test_narrator_session_rotated_span_name():
    """Constant must match the span name used by the GM dashboard wiring."""
    from sidequest.telemetry.spans import SPAN_NARRATOR_SESSION_ROTATED

    assert SPAN_NARRATOR_SESSION_ROTATED == "narrator.session_rotated"


def test_narrator_session_rotated_span_emits_with_reason_cli_error():
    from sidequest.telemetry.spans import (
        SPAN_NARRATOR_SESSION_ROTATED,
        narrator_session_rotated_span,
    )

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with narrator_session_rotated_span(
        reason="cli_error",
        cumulative_tokens=712341,
        turn_number=84,
        cli_error_signature="context_window_full",
        recap_chars=420,
        rebuild_latency_ms=1130,
        _tracer=t,
    ) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == SPAN_NARRATOR_SESSION_ROTATED

    attrs = spans[0].attributes or {}
    assert attrs.get("reason") == "cli_error"
    assert attrs.get("cumulative_tokens") == 712341
    assert attrs.get("turn_number") == 84
    assert attrs.get("cli_error_signature") == "context_window_full"
    assert attrs.get("recap_chars") == 420
    assert attrs.get("rebuild_latency_ms") == 1130


def test_narrator_session_rotated_span_emits_with_reason_token_threshold():
    """Proactive rotation (story 45-48) carries `threshold`, not `cli_error_signature`."""
    from sidequest.telemetry.spans import narrator_session_rotated_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with narrator_session_rotated_span(
        reason="token_threshold",
        cumulative_tokens=720000,
        turn_number=92,
        threshold=700000,
        recap_chars=380,
        rebuild_latency_ms=1240,
        _tracer=t,
    ):
        pass

    attrs = (exporter.get_finished_spans()[0].attributes) or {}
    assert attrs.get("reason") == "token_threshold"
    assert attrs.get("threshold") == 700000


def test_narrator_session_rotated_span_emits_with_reason_session_expired():
    from sidequest.telemetry.spans import narrator_session_rotated_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with narrator_session_rotated_span(
        reason="session_expired",
        cumulative_tokens=120000,
        turn_number=15,
        cli_error_signature="session_not_found",
        recap_chars=300,
        rebuild_latency_ms=890,
        _tracer=t,
    ):
        pass

    attrs = (exporter.get_finished_spans()[0].attributes) or {}
    assert attrs.get("reason") == "session_expired"
    assert attrs.get("cli_error_signature") == "session_not_found"


def test_narrator_session_rotated_span_emits_with_reason_unknown():
    from sidequest.telemetry.spans import narrator_session_rotated_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with narrator_session_rotated_span(
        reason="unknown",
        cumulative_tokens=540000,
        turn_number=63,
        cli_error_signature="some_brand_new_error_signature",
        recap_chars=210,
        rebuild_latency_ms=1410,
        _tracer=t,
    ):
        pass

    attrs = (exporter.get_finished_spans()[0].attributes) or {}
    assert attrs.get("reason") == "unknown"


def test_narrator_unrecoverable_span_name():
    """Companion span — emitted only when recovery itself fails (ADR-066 §8)."""
    from sidequest.telemetry.spans import SPAN_NARRATOR_UNRECOVERABLE

    assert SPAN_NARRATOR_UNRECOVERABLE == "narrator.unrecoverable"


def test_narrator_unrecoverable_span_emits():
    from sidequest.telemetry.spans import (
        SPAN_NARRATOR_UNRECOVERABLE,
        narrator_unrecoverable_span,
    )

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with narrator_unrecoverable_span(
        reason="rebuild_failed",
        first_error_signature="context_window_full",
        rebuild_error_signature="context_window_full_again",
        turn_number=84,
        _tracer=t,
    ):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == SPAN_NARRATOR_UNRECOVERABLE
    attrs = spans[0].attributes or {}
    assert attrs.get("reason") == "rebuild_failed"
    assert attrs.get("first_error_signature") == "context_window_full"
    assert attrs.get("rebuild_error_signature") == "context_window_full_again"
    assert attrs.get("turn_number") == 84
