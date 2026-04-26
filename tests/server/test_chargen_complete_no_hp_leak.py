"""Regression: chargen.complete must not leak the legacy `hp=N` field.

ADR-014 ("Diamonds and Coal") and ADR-078 ("Edge / Composure") removed the
hit-points field from CreatureCore in favor of an EdgePool. The chargen
completion path used to log ``chargen.complete ... hp=N`` and emit an OTEL
event with an ``hp`` key — both pulled from ``character.core.edge.current``
under a misleading name. Sebastien-axis (mechanics-first) players notice
this immediately and ask why HP is showing when the schema says it
shouldn't.

This regression test locks in the rename:

- The ``chargen.complete`` info-log line carries ``schema=adr-014`` and
  ``edge=current/max`` and contains no bare ``hp=`` substring.
- The OTEL ``character_creation.character_built`` event has no ``hp`` key
  and exposes ``edge_current`` / ``edge_max`` instead.
"""

from __future__ import annotations

import asyncio
import logging
import re
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


from tests.server.conftest import (  # noqa: E402
    mock_claude_client_factory as _mock_claude_client_factory,
)


@pytest.fixture
def save_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def handler_factory(save_dir: Path):
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("content pack not found")

    def make() -> WebSocketSessionHandler:
        return WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=save_dir,
        )

    return make


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


async def _connect(
    handler: WebSocketSessionHandler,
    *,
    player_name: str = "Sebastien",
    world: str = "grimvault",
) -> SessionEventMessage:
    payload = SessionEventPayload(
        event="connect",
        player_name=player_name,
        genre="caverns_and_claudes",
        world=world,
    )
    out = await handler.handle_message(SessionEventMessage(payload=payload, player_id=""))
    assert isinstance(out[0], SessionEventMessage)
    return out[0]


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


class TestChargenCompleteNoHpLeak:
    def test_chargen_complete_log_uses_edge_not_hp(
        self, handler_factory, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            with caplog.at_level(logging.INFO):
                await _walk_and_confirm(handler)

            chargen_lines = [
                rec.getMessage()
                for rec in caplog.records
                if rec.getMessage().startswith("chargen.complete")
            ]
            assert chargen_lines, "expected at least one chargen.complete log line"

            # Must carry the ADR-014 schema marker so future regressions are
            # grep-able in the playtest log corpus.
            assert any(
                "schema=adr-014" in line for line in chargen_lines
            ), f"chargen.complete missing schema=adr-014: {chargen_lines!r}"

            # No bare `hp=N` token (ADR-014 removed the HP field). We
            # specifically guard against the legacy `hp=10` template the
            # log used to render. A trailing `hp=` would be an
            # equally-bad regression.
            for line in chargen_lines:
                assert not re.search(
                    r"\bhp=\d", line
                ), f"chargen.complete leaks legacy hp= field: {line!r}"

            # Edge mechanical state must be present in some form (Sebastien
            # axis: mechanical visibility on completion).
            assert any(
                re.search(r"\bedge=\d+/\d+", line) for line in chargen_lines
            ), f"chargen.complete missing edge=N/M: {chargen_lines!r}"

        asyncio.run(body())

    def test_character_built_otel_event_has_no_hp_key(
        self, handler_factory, otel_capture: InMemorySpanExporter
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_and_confirm(handler)

            built_events = _events(
                otel_capture, "character_creation.character_built"
            )
            assert built_events, (
                "expected at least one character_creation.character_built event"
            )

            for ev in built_events:
                attrs = dict(ev.attributes or {})
                assert "hp" not in attrs, (
                    f"character_built event leaks legacy `hp` key: {attrs!r}"
                )
                # ADR-014 fields must be present so the dashboard can show
                # the actual schema.
                assert "edge_current" in attrs, (
                    f"character_built event missing edge_current: {attrs!r}"
                )
                assert "edge_max" in attrs, (
                    f"character_built event missing edge_max: {attrs!r}"
                )
                assert attrs.get("schema") == "adr-014"

        asyncio.run(body())
