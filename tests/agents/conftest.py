"""Shared fixtures for tests/agents/ and its subdirectories."""

from __future__ import annotations

import pytest

from sidequest.agents.prompt_framework.core import PromptRegistry
from sidequest.game.npc_pool import NpcPoolMember


@pytest.fixture
def build_registry():
    def _build():
        return PromptRegistry()

    return _build


@pytest.fixture
def minimal_npc_pool() -> list[NpcPoolMember]:
    """A small pool list with one named NPC.

    Story 45-52 cleanup: ``GameSnapshot.npc_registry`` was dropped; the
    canonical identity-only cast pool is ``GameSnapshot.npc_pool``
    (``list[NpcPoolMember]``). Last-seen state, when needed, lives on
    a promoted ``Npc`` (``GameSnapshot.npcs``).
    """
    return [
        NpcPoolMember(
            name="Harlan",
            role="innkeeper",
            pronouns="he/him",
            appearance="grey beard, apron",
            drawn_from="world_authored",
        ),
    ]


@pytest.fixture
def simple_turn_context():
    """Minimal TurnContext for turn 0 (opening turn)."""
    from sidequest.agents.orchestrator import TurnContext

    return TurnContext(
        character_name="Kael",
        genre="caverns_and_claudes",
        turn_number=0,
    )


@pytest.fixture
def simple_turn_context_turn_three():
    """Minimal TurnContext for turn 3 (mid-session, post-opening)."""
    from sidequest.agents.orchestrator import TurnContext

    return TurnContext(
        character_name="Kael",
        genre="caverns_and_claudes",
        turn_number=3,
    )


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
