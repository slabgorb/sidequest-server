"""Room-graph init integration — Story 2.3 Slice E.

Drives the chargen confirmation path against caverns_and_claudes and
asserts that when the selected world uses ``navigation_mode:
room_graph``, ``snap.location`` lands on the entrance room id and
OTEL emits ``location.initialized``. Region-mode worlds (no
cartography rooms) no-op. A room-graph world with a stripped entrance
logs-and-continues without hard-failing confirmation — the player is
mid-commit and a broken pack mustn't drop them on the floor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from tests.server.conftest import (
    mock_claude_client_factory as _mock_claude_client_factory,
)

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


@pytest.fixture
def handler_factory(tmp_path: Path):
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("content pack not found")

    def make() -> WebSocketSessionHandler:
        return WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=tmp_path,
        )

    return make


@pytest.fixture
def otel_capture():
    """Attach an in-memory exporter to the running TracerProvider.

    The server installs a singleton TracerProvider at import time
    (``sidequest.telemetry.setup.init_tracer``). OpenTelemetry refuses
    to swap that provider once set, so the fixture instead mounts an
    additional :class:`SimpleSpanProcessor` alongside the existing
    processors — the handler's ``span.add_event(...)`` calls fan out
    to both console + in-memory sinks for the duration of the test.
    """
    from sidequest.telemetry.setup import init_tracer

    init_tracer()  # idempotent
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), (
        f"expected SDK TracerProvider, got {type(provider)!r}"
    )
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


async def _connect(
    handler: WebSocketSessionHandler,
    *,
    world: str = "grimvault",
) -> None:
    from tests.server.conftest import attach_default_room_context, seed_slug_for_test

    slug = seed_slug_for_test(handler._save_dir, genre="caverns_and_claudes", world=world)
    attach_default_room_context(handler)
    payload = SessionEventPayload(
        event="connect",
        player_name="Tester",
        game_slug=slug,
    )
    out = await handler.handle_message(SessionEventMessage(payload=payload, player_id=""))
    assert isinstance(out[0], SessionEventMessage)


async def _walk_and_confirm(handler: WebSocketSessionHandler) -> list:
    """Walk the chargen builder through Confirmation and commit.

    Opens a tracer span around the commit so ``span.add_event(...)``
    calls inside ``_chargen_confirmation`` land on a recording span
    the test fixture can scrape events from. Without an active span
    those calls silently drop (invalid span / no-op).
    """
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
    return [e for span in exporter.get_finished_spans() for e in span.events if e.name == name]


class TestRoomGraphInit:
    def test_grimvault_confirmation_lands_on_entrance(
        self, handler_factory, otel_capture: InMemorySpanExporter
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler, world="grimvault")
            sd = handler._session_data  # type: ignore[attr-defined]

            # Sanity: grimvault is actually a room_graph world in content.
            from sidequest.genre.models.world import NavigationMode

            world = sd.genre_pack.worlds.get("grimvault")
            assert world is not None
            assert world.cartography.navigation_mode == NavigationMode.room_graph
            assert world.cartography.rooms, "grimvault must load rooms.yaml"

            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"

            # Entrance room id populated on the snapshot.
            assert sd.snapshot.location
            entrance_id = sd.snapshot.location
            entrance_room = next(r for r in world.cartography.rooms if r.id == entrance_id)
            assert entrance_room.room_type == "entrance"
            assert entrance_id in sd.snapshot.discovered_rooms

            # OTEL: location.initialized fired with the canonical fields.
            events = _events(otel_capture, "location.initialized")
            assert len(events) == 1
            attrs = dict(events[0].attributes or {})
            assert attrs["location"] == entrance_id
            assert attrs["mode"] == "room_graph"
            assert attrs["source"] == "entrance_room"
            assert attrs["genre"] == "caverns_and_claudes"
            assert attrs["world"] == "grimvault"

            # No init_failed event on the happy path.
            assert _events(otel_capture, "location.init_failed") == []

        asyncio.run(body())

    def test_region_mode_world_is_noop(
        self, handler_factory, otel_capture: InMemorySpanExporter
    ) -> None:
        """A world the handler can't resolve (e.g. legacy flickering_reach
        test alias, or a region-mode world) must not emit
        ``location.initialized`` — the rules-based default_location path
        handles that and lands in a later slice."""

        async def body() -> None:
            handler = handler_factory()
            # flickering_reach doesn't exist in content — pack.worlds.get
            # returns None, which disables the room-graph branch.
            await _connect(handler, world="flickering_reach")
            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)

            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd.genre_pack.worlds.get("flickering_reach") is None

            assert _events(otel_capture, "location.initialized") == []
            assert _events(otel_capture, "location.init_failed") == []

        asyncio.run(body())

    def test_room_graph_with_no_entrance_logs_and_continues(
        self,
        handler_factory,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        """Pack authoring bug path: room_graph mode + rooms loaded but
        none tagged ``entrance``. Confirmation must complete and the
        error must surface as a log + OTEL event — never a dispatch
        crash that strands the player mid-commit."""

        async def body() -> None:
            handler = handler_factory()
            await _connect(handler, world="grimvault")
            sd = handler._session_data  # type: ignore[attr-defined]

            # Strip the entrance marker in place — every room becomes 'normal'.
            world = sd.genre_pack.worlds.get("grimvault")
            assert world is not None and world.cartography.rooms
            for room in world.cartography.rooms:
                if room.room_type == "entrance":
                    room.room_type = "normal"

            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"

            # Snapshot location is not one of the (now entrance-less)
            # room ids — room-graph init can't have succeeded. Whatever
            # materialize_from_genre_pack wrote survives unchanged
            # (scene context from history.yaml). The rules-based
            # default_location wiring in a later slice is what replaces
            # that with a canonical region id.
            room_ids = {r.id for r in world.cartography.rooms or []}
            assert sd.snapshot.location not in room_ids

            # OTEL: init_failed fired; init success did not.
            assert _events(otel_capture, "location.initialized") == []
            failed = _events(otel_capture, "location.init_failed")
            assert len(failed) == 1
            attrs = dict(failed[0].attributes or {})
            assert attrs["mode"] == "room_graph"
            assert "no entrance" in attrs["error"]

        asyncio.run(body())
