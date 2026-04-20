"""Lore-seeding integration — Story 2.3 Slice F.

Drives chargen confirmation against caverns_and_claudes/grimvault and
asserts:

- ``sd.lore_store`` ends up non-empty after commit
- fragments carry :class:`LoreSource.CharacterCreation` (not the genre
  pack's generic lore — that's a later slice when the narrator boot
  path picks up its own store)
- ids match the Rust ``lore_char_creation_<scene_id>_<choice_index>``
  format
- OTEL emits ``lore.char_creation_seeded`` with the expected counts

The seeding call fires BEFORE ``sd.builder = None`` so the scene list
is available — covered by the successful count assertion.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.lore_store import LoreSource
from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler


CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


def _mock_claude_client_factory():
    mock = MagicMock()
    mock.send_with_session = AsyncMock()
    return lambda: mock


@pytest.fixture
def handler(tmp_path: Path) -> WebSocketSessionHandler:
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("content pack not found")
    return WebSocketSessionHandler(
        claude_client_factory=_mock_claude_client_factory(),
        genre_pack_search_paths=[CONTENT_ROOT],
        save_dir=tmp_path,
    )


@pytest.fixture
def otel_capture():
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


async def _walk_and_confirm(handler: WebSocketSessionHandler) -> list:
    sd = handler._session_data  # type: ignore[attr-defined]
    builder = sd.builder
    assert builder is not None

    while not builder.is_confirmation():
        scene = builder.current_scene()
        if scene.choices:
            payload = CharacterCreationPayload(phase="scene", choice="1")
        elif scene.allows_freeform:
            payload = CharacterCreationPayload(phase="scene", choice="Rux")
        else:
            payload = CharacterCreationPayload(phase="continue")
        out = await handler.handle_message(
            CharacterCreationMessage(payload=payload, player_id="pid")
        )
        if out and isinstance(out[0], ErrorMessage):
            raise AssertionError(f"walk error: {out[0].payload.message}")

    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("chargen_confirmation"):
        return await handler.handle_message(
            CharacterCreationMessage(
                payload=CharacterCreationPayload(phase="confirmation"),
                player_id="pid",
            )
        )


def _events(exporter: InMemorySpanExporter, name: str) -> list:
    return [
        e
        for span in exporter.get_finished_spans()
        for e in span.events
        if e.name == name
    ]


class TestLoreSeedingDispatch:
    def test_grimvault_confirmation_seeds_lore_store(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await handler.handle_message(
                SessionEventMessage(
                    payload=SessionEventPayload(
                        event="connect",
                        player_name="Tester",
                        genre="caverns_and_claudes",
                        world="grimvault",
                    ),
                    player_id="",
                )
            )
            sd = handler._session_data  # type: ignore[attr-defined]
            # Session starts with an empty lore store.
            assert sd.lore_store.is_empty()

            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"

            # Post-confirmation: lore store has fragments from every
            # chargen scene's choices.
            assert not sd.lore_store.is_empty()
            for frag in sd.lore_store:
                assert frag.source == LoreSource.CharacterCreation
                assert frag.id.startswith("lore_char_creation_")
                assert frag.content  # label + ": " + description

        asyncio.run(body())

    def test_confirmation_emits_otel_lore_seeded(
        self,
        handler: WebSocketSessionHandler,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        async def body() -> None:
            await handler.handle_message(
                SessionEventMessage(
                    payload=SessionEventPayload(
                        event="connect",
                        player_name="Tester",
                        genre="caverns_and_claudes",
                        world="grimvault",
                    ),
                    player_id="",
                )
            )
            await _walk_and_confirm(handler)
            sd = handler._session_data  # type: ignore[attr-defined]

            events = _events(otel_capture, "lore.char_creation_seeded")
            assert len(events) == 1
            attrs = dict(events[0].attributes or {})
            assert attrs["event"] == "char_creation_lore_seeded"
            assert attrs["fragments_added"] == len(sd.lore_store)
            assert attrs["total_fragments"] == len(sd.lore_store)
            assert attrs["total_tokens"] == sd.lore_store.total_tokens()
            assert attrs["genre"] == "caverns_and_claudes"
            assert attrs["world"] == "grimvault"

        asyncio.run(body())
