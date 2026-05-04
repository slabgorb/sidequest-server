"""Wiring tests for OTLP exporter setup — Story 45-41.

The 2026-05-03 playtest discovered that the Jaeger sidecar receives
zero spans during a 3-hour gameplay session even though `just up` runs
the server normally. Root cause: the OTLP exporter and the watcher→span
bridge are both gated behind environment variables (`SIDEQUEST_OTLP_ENDPOINT`
and `SIDEQUEST_WATCHER_AS_SPANS`) that `just up` does NOT set. Only the
opt-in `just up-traced` recipe enables them.

This is exactly the silent-fallback failure mode CLAUDE.md's
"OTEL Observability Principle" was written to prevent — the GM panel
becomes a useless lie detector when the operator can't tell whether
spans aren't flowing because nothing happened or because the wiring is
dormant.

These tests pin two behaviors:

1. **Loud-fail on dormant OTLP** — when `init_tracer()` runs without
   the OTLP env, it must log a single, clear notice so the server log
   shows the operator that traces are NOT flowing to any collector.
2. **Loud-fail on half-wired state** — when OTLP is configured but the
   watcher→span bridge is off, the server log must say so. Otherwise
   Jaeger gets a stub trail (only direct `tracer().start_as_current_span`
   calls) and the operator thinks observability is healthy when most
   semantic events are invisible.
3. **End-to-end span emission** — when both gates are on, calling
   `publish_event(...)` produces a span that reaches the registered
   span processor (the OTLP exporter substitute in real deployments).
"""

from __future__ import annotations

import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.telemetry import setup as telemetry_setup
from sidequest.telemetry import watcher_hub


@pytest.fixture(autouse=True)
def _reset_tracer_init() -> None:
    """Force ``init_tracer`` to re-run every test so env-var changes take effect.

    ``init_tracer`` is idempotent in production (one-shot per process). For
    these tests we need it to honor the per-test monkeypatched env, so we
    flip the cached ``_initialized`` flag back to False before each test.
    """
    telemetry_setup._initialized = False
    yield
    telemetry_setup._initialized = False


@pytest.fixture
def captured_provider(monkeypatch: pytest.MonkeyPatch) -> dict[str, TracerProvider | None]:
    """Capture the TracerProvider ``init_tracer`` tries to install.

    OTEL's global ``set_tracer_provider`` is set-once: after the first call
    it logs ``"Overriding of current TracerProvider is not allowed"`` and
    noops. That makes ``init_tracer`` impossible to test in isolation
    against the real global. We intercept ``set_tracer_provider`` inside
    the ``setup`` module so the test can inspect the provider init_tracer
    builds without fighting OTEL's global lock.
    """
    captured: dict[str, TracerProvider | None] = {"provider": None}

    def _capture(provider: TracerProvider) -> None:
        captured["provider"] = provider

    monkeypatch.setattr(telemetry_setup.trace, "set_tracer_provider", _capture)
    return captured


def _install_in_memory_exporter_on_global() -> InMemorySpanExporter:
    """Cooperate with whatever global provider OTEL has and add an exporter.

    Mirrors the pattern in test_watcher_event_spans.py — once OTEL has a
    real SDK provider installed, we can keep adding processors to it for
    each test without fighting the set-once guard.
    """
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


# ---------------------------------------------------------------------------
# Loud-fail tests — Story 45-41 RED phase
# ---------------------------------------------------------------------------


def test_init_tracer_warns_when_otlp_endpoint_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`init_tracer()` without `SIDEQUEST_OTLP_ENDPOINT` must log a notice.

    Today this path is silent — the server starts, the OTLP exporter is
    never registered, and the operator has no log signal that Jaeger
    won't see any traces. The fix must surface a single, clear log
    line at INFO or WARNING level so `tail -f /tmp/sidequest-server.log`
    shows the dormant-OTLP state.
    """
    monkeypatch.delenv("SIDEQUEST_OTLP_ENDPOINT", raising=False)
    caplog.set_level(logging.INFO, logger="sidequest.telemetry.setup")

    telemetry_setup.init_tracer()

    dormant_messages = [
        rec
        for rec in caplog.records
        if "dormant" in rec.getMessage().lower() or "otlp" in rec.getMessage().lower()
    ]
    assert dormant_messages, (
        "Expected init_tracer to log an OTLP-dormant notice when "
        "SIDEQUEST_OTLP_ENDPOINT is unset; got no matching log records. "
        f"All records: {[r.getMessage() for r in caplog.records]}"
    )
    # The message must mention the env var so the operator can fix it without
    # grepping source.
    assert any("SIDEQUEST_OTLP_ENDPOINT" in r.getMessage() for r in dormant_messages), (
        "OTLP-dormant log message must name the env var the operator should set."
    )


def test_init_tracer_warns_when_otlp_set_but_watcher_synth_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`init_tracer()` with OTLP set but watcher-synth off must warn.

    Half-wired state: tracer-driven spans flow to Jaeger (turn root spans,
    explicit `start_as_current_span` calls) but every `publish_event(...)`
    call is invisible because the bridge is off. The dashboard works, but
    the forensic Jaeger trail is missing the entire semantic event stream
    (NPC reinventions, render gaps, confrontation triggers). The server
    log must say so at startup.
    """
    monkeypatch.setenv("SIDEQUEST_OTLP_ENDPOINT", "localhost:4317")
    monkeypatch.delenv("SIDEQUEST_WATCHER_AS_SPANS", raising=False)
    caplog.set_level(logging.INFO, logger="sidequest.telemetry.setup")

    telemetry_setup.init_tracer()

    half_wired = [
        rec
        for rec in caplog.records
        if "SIDEQUEST_WATCHER_AS_SPANS" in rec.getMessage()
        or "watcher" in rec.getMessage().lower()
        and ("disabled" in rec.getMessage().lower() or "off" in rec.getMessage().lower())
    ]
    assert half_wired, (
        "Expected init_tracer to warn about half-wired state when OTLP is "
        "set but SIDEQUEST_WATCHER_AS_SPANS is unset. Got no matching "
        f"records. All records: {[r.getMessage() for r in caplog.records]}"
    )


def test_init_tracer_silent_dormant_notice_does_not_fire_when_fully_wired(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When both env vars are set, no dormant/half-wired warnings.

    Negative case for the two warnings above — when configuration is
    correct, the operator should NOT see dormant/half-wired noise in
    the log. (We still expect the existing
    ``otel.otlp_exporter_registered`` info line.)
    """
    monkeypatch.setenv("SIDEQUEST_OTLP_ENDPOINT", "localhost:4317")
    monkeypatch.setenv("SIDEQUEST_WATCHER_AS_SPANS", "1")
    caplog.set_level(logging.INFO, logger="sidequest.telemetry.setup")

    telemetry_setup.init_tracer()

    msgs = [r.getMessage().lower() for r in caplog.records]
    dormant = [m for m in msgs if "dormant" in m]
    assert not dormant, f"Did not expect 'dormant' log noise when fully wired; got: {dormant}"
    half_wired = [
        m for m in msgs if "half" in m or "bridge_disabled" in m or "watcher_as_spans=off" in m
    ]
    assert not half_wired, (
        f"Did not expect half-wired warning when watcher synth IS enabled; got: {half_wired}"
    )


# ---------------------------------------------------------------------------
# End-to-end wiring test — semantic event must reach a span processor.
# ---------------------------------------------------------------------------


def test_publish_event_lands_in_span_processor_when_synth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring test: publish_event → tracer → exporter end-to-end.

    Substitutes the OTLPSpanExporter with an InMemorySpanExporter on
    the active global TracerProvider so we can assert on the span
    without needing a live Jaeger sidecar. This is the wiring test
    required by CLAUDE.md "Every Test Suite Needs a Wiring Test" — it
    traverses the same code path that ships to production.
    """
    exporter = _install_in_memory_exporter_on_global()
    monkeypatch.setenv("SIDEQUEST_WATCHER_AS_SPANS", "1")

    watcher_hub.publish_event(
        "turn_complete",
        {"turn_id": "t-wiring-1", "round": 3, "actor_count": 2},
        component="orchestrator",
        severity="info",
    )

    spans = [s for s in exporter.get_finished_spans() if s.name == "watcher.turn_complete"]
    assert spans, (
        "Expected at least one 'watcher.turn_complete' span in the exporter "
        f"after publish_event; got {[s.name for s in exporter.get_finished_spans()]}"
    )
    span = spans[-1]
    attrs = span.attributes or {}
    assert attrs.get("watcher.event_type") == "turn_complete"
    assert attrs.get("watcher.component") == "orchestrator"
    assert attrs.get("field.turn_id") == "t-wiring-1"
    assert attrs.get("field.round") == 3
    assert attrs.get("field.actor_count") == 2


def test_publish_event_does_not_reach_exporter_when_synth_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative wiring case — without the bridge env var, no span lands.

    Pins the current default behavior so the loud-fail warning fix
    above doesn't accidentally also flip the bridge default. If we
    DO want to default-on the bridge, that's a separate decision and
    this test should be updated in the same change.
    """
    exporter = _install_in_memory_exporter_on_global()
    monkeypatch.delenv("SIDEQUEST_WATCHER_AS_SPANS", raising=False)

    watcher_hub.publish_event(
        "turn_complete",
        {"turn_id": "t-wiring-2"},
        component="orchestrator",
        severity="info",
    )

    bridge_spans = [s for s in exporter.get_finished_spans() if s.name.startswith("watcher.")]
    assert not bridge_spans, (
        f"Expected no watcher.* spans when bridge env unset, got: {[s.name for s in bridge_spans]}"
    )


# ---------------------------------------------------------------------------
# OTLPSpanExporter registration — pins the actual export pipeline path.
# ---------------------------------------------------------------------------


def test_init_tracer_registers_otlp_exporter_when_endpoint_set(
    monkeypatch: pytest.MonkeyPatch,
    captured_provider: dict[str, TracerProvider | None],
) -> None:
    """When SIDEQUEST_OTLP_ENDPOINT is set, an OTLPSpanExporter is wired in.

    Walks the provider's processor chain to confirm at least one
    OTLPSpanExporter is registered. This is the "is the wire even
    plugged in" check — without it, all the env-var probing in the
    world won't help if init_tracer's code path never instantiates
    the exporter.

    Uses ``captured_provider`` to dodge OTEL's set-once global; we
    intercept the provider init_tracer builds rather than fighting
    the global lock.
    """
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )

    monkeypatch.setenv("SIDEQUEST_OTLP_ENDPOINT", "localhost:4317")
    telemetry_setup.init_tracer()

    provider = captured_provider["provider"]
    assert isinstance(provider, TracerProvider), (
        "init_tracer should construct and install a TracerProvider"
    )
    multi = getattr(provider, "_active_span_processor", None)
    processors = getattr(multi, "_span_processors", ()) if multi else ()
    has_otlp = any(
        isinstance(getattr(p, "span_exporter", None), OTLPSpanExporter) for p in processors
    )
    assert has_otlp, (
        "Expected an OTLPSpanExporter on the constructed TracerProvider when "
        f"SIDEQUEST_OTLP_ENDPOINT is set; processors: {[type(p).__name__ for p in processors]}"
    )
