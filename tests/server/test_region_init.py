"""Region-init integration — Story 37-31.

Drives the chargen confirmation path against real content packs and
asserts that ``snap.current_region`` lands on the world's
``cartography.starting_region`` at turn 1 for both ``region`` and
``room_graph`` navigation modes. OTEL ``region.initialized`` must
emit with the canonical fields so the GM panel can verify the Map
tab is load-bearing from the opening scene.

Wiring test: loads a real world, walks chargen to completion, and
confirms the region is non-blank on the snapshot the narrator will
see. Without the init, ``current_region`` stays ``""`` and the Map
tab is useless on turn 1.
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


CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


from tests.server.conftest import mock_claude_client_factory as _mock_claude_client_factory  # noqa: E402


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
    genre: str,
    world: str,
) -> None:
    payload = SessionEventPayload(
        event="connect",
        player_name="Tester",
        genre=genre,
        world=world,
    )
    out = await handler.handle_message(SessionEventMessage(payload=payload, player_id=""))
    assert isinstance(out[0], SessionEventMessage)


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


class TestRegionInit:
    def test_room_graph_world_populates_region_from_cartography(
        self, handler_factory, otel_capture: InMemorySpanExporter
    ) -> None:
        """Grimvault is room_graph mode but still declares
        ``starting_region: ashgate_square``. current_region must land on
        it so the Map tab surfaces a region label from turn 1."""

        async def body() -> None:
            handler = handler_factory()
            await _connect(handler, genre="caverns_and_claudes", world="grimvault")
            sd = handler._session_data  # type: ignore[attr-defined]

            world = sd.genre_pack.worlds.get("grimvault")
            assert world is not None
            expected_region = world.cartography.starting_region
            assert expected_region, "grimvault cartography must declare a starting_region"

            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"

            # current_region populated and present in discovered_regions.
            assert sd.snapshot.current_region == expected_region, (
                f"turn 1 current_region must be '{expected_region}', "
                f"got '{sd.snapshot.current_region}'"
            )
            assert expected_region in sd.snapshot.discovered_regions

            # OTEL: region.initialized fired with the canonical fields.
            events = _events(otel_capture, "region.initialized")
            assert len(events) == 1, (
                f"expected exactly one region.initialized event, got {len(events)}"
            )
            attrs = dict(events[0].attributes or {})
            assert attrs["region"] == expected_region
            assert attrs["mode"] == "room_graph"
            assert attrs["source"] == "starting_region"
            assert attrs["genre"] == "caverns_and_claudes"
            assert attrs["world"] == "grimvault"

            # No init_failed on the happy path.
            assert _events(otel_capture, "region.init_failed") == []

        asyncio.run(body())

    def test_region_mode_world_populates_current_region(
        self, handler_factory, otel_capture: InMemorySpanExporter
    ) -> None:
        """A region-mode world (heavy_metal / evropi) must land
        current_region on cartography.starting_region — the Map tab
        has nothing else to render from on turn 1."""

        if not (CONTENT_ROOT / "heavy_metal" / "worlds" / "evropi").is_dir():
            pytest.skip("heavy_metal/evropi content not available")

        async def body() -> None:
            handler = handler_factory()
            await _connect(handler, genre="heavy_metal", world="evropi")
            sd = handler._session_data  # type: ignore[attr-defined]

            world = sd.genre_pack.worlds.get("evropi")
            assert world is not None
            expected_region = world.cartography.starting_region
            assert expected_region

            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"

            assert sd.snapshot.current_region == expected_region
            assert expected_region in sd.snapshot.discovered_regions

            events = _events(otel_capture, "region.initialized")
            assert len(events) == 1
            attrs = dict(events[0].attributes or {})
            assert attrs["region"] == expected_region
            assert attrs["mode"] == "region"

        asyncio.run(body())

    def test_blank_starting_region_logs_and_continues(
        self, handler_factory, otel_capture: InMemorySpanExporter
    ) -> None:
        """Pack authoring bug: cartography declares no starting_region.
        Confirmation must still complete, with an OTEL init_failed
        event and current_region left blank — never a dispatch crash
        that strands the player mid-commit."""

        async def body() -> None:
            handler = handler_factory()
            await _connect(handler, genre="caverns_and_claudes", world="grimvault")
            sd = handler._session_data  # type: ignore[attr-defined]

            # Wipe the starting_region in place to simulate an authoring bug.
            world = sd.genre_pack.worlds.get("grimvault")
            assert world is not None
            world.cartography.starting_region = ""

            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"

            # current_region stayed blank and no region was discovered.
            assert sd.snapshot.current_region == ""
            assert sd.snapshot.discovered_regions == []

            # OTEL: init_failed fired; init success did not.
            assert _events(otel_capture, "region.initialized") == []
            failed = _events(otel_capture, "region.init_failed")
            assert len(failed) == 1
            attrs = dict(failed[0].attributes or {})
            assert attrs["mode"] == "room_graph"
            assert "blank" in attrs["error"]

        asyncio.run(body())

    def test_unknown_starting_region_logs_and_continues(
        self, handler_factory, otel_capture: InMemorySpanExporter
    ) -> None:
        """Pack authoring bug: cartography declares a starting_region
        that is not a key in its regions map. Fail loud via OTEL, do
        not hard-fail the confirmation frame."""

        async def body() -> None:
            handler = handler_factory()
            await _connect(handler, genre="caverns_and_claudes", world="grimvault")
            sd = handler._session_data  # type: ignore[attr-defined]

            world = sd.genre_pack.worlds.get("grimvault")
            assert world is not None
            assert world.cartography.regions, "grimvault must declare regions"
            world.cartography.starting_region = "not_a_real_region"

            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"

            assert sd.snapshot.current_region == ""
            assert _events(otel_capture, "region.initialized") == []
            failed = _events(otel_capture, "region.init_failed")
            assert len(failed) == 1
            attrs = dict(failed[0].attributes or {})
            assert "not_a_real_region" in attrs["error"]

        asyncio.run(body())
