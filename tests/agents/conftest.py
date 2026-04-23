"""Shared fixtures for tests/agents/ and its subdirectories."""
from __future__ import annotations

import pytest

from sidequest.game.session import NpcRegistryEntry


@pytest.fixture
def minimal_npc_registry() -> list[NpcRegistryEntry]:
    """A small registry list with one named NPC.

    The Python port stores the NPC registry as ``list[NpcRegistryEntry]`` on
    ``WorldSnapshot.npc_registry`` (see ``sidequest/game/session.py``).
    """
    return [
        NpcRegistryEntry(
            name="Harlan",
            role="innkeeper",
            pronouns="he/him",
            appearance="grey beard, apron",
            last_seen_location="the inn",
            last_seen_turn=1,
        ),
    ]


@pytest.fixture
def otel_capture():
    """Capture spans emitted to the live OTEL tracer provider singleton.

    Matches the pattern in tests/server/test_chargen_persist_and_play.py —
    the ``tracer()`` helper used inside our span context managers closes over
    the global provider, so patching a different symbol won't reroute spans.
    Installing a SimpleSpanProcessor on the live singleton is the reliable
    way to observe spans emitted by production code paths.
    """
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()
