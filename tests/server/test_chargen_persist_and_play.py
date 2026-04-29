"""Chargen persistence + state transition integration — Slice G.

Drives the full chargen walk against caverns_and_claudes/grimvault
and asserts:

- After confirmation, ``sd.snapshot`` is persisted to SQLite — a
  reconnect with the same genre/world/player_name sees
  ``has_character=True`` and skips the builder.
- Session state flips from ``Creating`` to ``Playing`` at
  confirmation (not at first PLAYER_ACTION).
- ``snapshot.npc_registry`` is cleared at confirmation and OTEL
  emits ``npc_registry.cleared_on_chargen_complete``.
- The OTEL persist event ``session.persisted_at_chargen_complete``
  fires with the session identity.
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

from sidequest.game.session import NpcRegistryEntry
from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler, _State

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


from tests.server.conftest import (
    mock_claude_client_factory as _mock_claude_client_factory,  # noqa: E402
)


@pytest.fixture
def save_dir(tmp_path: Path) -> Path:
    """Per-test save directory so reconnects in one test see their own SQLite."""
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
    player_name: str = "Persistent",
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChargenPersistAndPlay:
    def test_confirmation_flips_state_to_playing(
        self, handler_factory
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            assert handler._state == _State.Creating  # type: ignore[attr-defined]

            out = await _walk_and_confirm(handler)
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"

            assert handler._state == _State.Playing  # type: ignore[attr-defined]

        asyncio.run(body())

    def test_confirmation_persists_snapshot_to_sqlite(
        self, handler_factory
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_and_confirm(handler)

            sd = handler._session_data  # type: ignore[attr-defined]
            # Load via the same store handle — bypasses the reconnect
            # path to isolate the persistence assertion.
            saved = sd.store.load()
            assert saved is not None
            assert saved.snapshot.characters, (
                "persisted snapshot must carry the built character"
            )
            assert saved.snapshot.characters[0].core.name
            assert saved.snapshot.genre_slug == "caverns_and_claudes"
            assert saved.snapshot.world_slug == "grimvault"

        asyncio.run(body())

    def test_reconnect_skips_chargen_with_has_character_true(
        self, handler_factory
    ) -> None:
        """End-to-end reconnect: walk chargen on handler #1, drop the
        connection, then open handler #2 against the same save dir with
        the same (genre, world, player_name). The second connect must
        see has_character=True in the connected event and skip
        initializing a builder."""

        async def body() -> None:
            # Connection 1: full chargen + confirmation.
            h1 = handler_factory()
            await _connect(h1)
            await _walk_and_confirm(h1)
            # Close the store so the second handler opens a fresh SQLite
            # connection against the same .db file.
            await h1.cleanup()

            # Connection 2: same player, same world → reconnect.
            h2 = handler_factory()
            connected = await _connect(h2)
            assert connected.payload.event == "connected"
            assert connected.payload.has_character is True

            sd2 = h2._session_data  # type: ignore[attr-defined]
            assert sd2.builder is None, "reconnect must not initialize a builder"
            assert sd2.snapshot.characters, (
                "reconnect must load the persisted character"
            )
            # Resumed session is already Playing — no chargen path to walk.
            assert h2._state == _State.Playing  # type: ignore[attr-defined]

        asyncio.run(body())

    def test_npc_registry_cleared_at_confirmation_with_otel(
        self,
        handler_factory,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        """The clear itself is defensive — ``materialize_from_genre_pack``
        swaps in a fresh snapshot mid-confirmation, so a test that seeds
        the registry before the walk can't observe nonzero previous_len.
        What matters: the clear fires (registry empty) and the OTEL
        event carries the expected identity attributes so the GM panel
        can confirm the chargen narrative reset ran."""

        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            sd = handler._session_data  # type: ignore[attr-defined]

            await _walk_and_confirm(handler)

            # Post-confirmation the registry is always empty.
            assert sd.snapshot.npc_registry == []
            # Sanity: the type-check also passes — the clear doesn't swap
            # in a raw list of something weird.
            for entry in sd.snapshot.npc_registry:  # pragma: no cover — empty
                assert isinstance(entry, NpcRegistryEntry)

            events = _events(
                otel_capture, "npc_registry.cleared_on_chargen_complete"
            )
            assert len(events) == 1
            attrs = dict(events[0].attributes or {})
            assert attrs["reason"] == "fresh_character_narrative_reset"
            assert attrs["genre"] == "caverns_and_claudes"
            assert attrs["world"] == "grimvault"
            assert "previous_len" in attrs  # numeric, may be 0 in chargen path

        asyncio.run(body())

    def test_otel_session_persisted_event_fires(
        self,
        handler_factory,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_and_confirm(handler)

            events = _events(otel_capture, "session.persisted_at_chargen_complete")
            assert len(events) == 1
            attrs = dict(events[0].attributes or {})
            assert attrs["event"] == "session.persisted"
            assert attrs["genre"] == "caverns_and_claudes"
            assert attrs["world"] == "grimvault"
            assert "turn" in attrs

        asyncio.run(body())

    # -----------------------------------------------------------------
    # Story 45-12: starting-kit dedup wire-test
    # -----------------------------------------------------------------

    def test_chargen_confirm_persists_deduped_inventory(
        self, handler_factory
    ) -> None:
        """AC6 wire-test: end-to-end chargen confirms produce a
        deduplicated inventory and the deduped result is what's
        persisted to SQLite. The Blutka regression evidence (Playtest
        3, 2026-04-19) was a 24-item starting kit; the
        ``caverns_and_claudes/grimvault`` pack itself ships
        ``starting_equipment[Delver]`` with 3 torches and 2 rations
        intra-list — so a successful chargen-confirm must collapse those
        before save. This catches the half-wired regression where dedup
        runs in-memory but the persisted snapshot still has stale items.

        Invariant: after confirmation, the persisted snapshot's
        inventory MUST have no duplicate ids and no duplicate
        (case-insensitive) names.
        """

        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_and_confirm(handler)

            sd = handler._session_data  # type: ignore[attr-defined]
            saved = sd.store.load()
            assert saved is not None, (
                "chargen-confirm must persist the snapshot to SQLite"
            )
            assert saved.snapshot.characters, (
                "persisted snapshot must carry the built character"
            )

            items = saved.snapshot.characters[0].core.inventory.items

            # The bug-evidence shape: Blutka shipped 24 items; the
            # spec count is 13. The persisted inventory must NOT
            # exceed the catalogue spec count + any disjoint
            # builder-side items. The strict invariant is uniqueness.
            seen_ids: set[str] = set()
            for item in items:
                iid = str(item.get("id", "")).strip().lower()
                if not iid:
                    continue
                assert iid not in seen_ids, (
                    f"Persisted inventory contains DUPLICATE id "
                    f"{iid!r}. Items: {[i.get('id') for i in items]!r}. "
                    f"Dedup did not run, OR ran in-memory but the "
                    f"persisted snapshot still has stale items "
                    f"(half-wired regression)."
                )
                seen_ids.add(iid)

            seen_names: set[str] = set()
            for item in items:
                iname = str(item.get("name", "")).strip().lower()
                if not iname:
                    continue
                assert iname not in seen_names, (
                    f"Persisted inventory contains DUPLICATE name "
                    f"{iname!r}. Names: "
                    f"{[i.get('name') for i in items]!r}."
                )
                seen_names.add(iname)

        asyncio.run(body())

    def test_chargen_confirm_emits_starting_kit_dedup_evaluated_span(
        self,
        handler_factory,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        """AC5/AC6 wire-test: the production chargen-confirm path MUST
        emit ``chargen.starting_kit_dedup_evaluated`` so Sebastien's GM
        panel sees the dedup pass ran. Without this assertion, dedup
        could be implemented in the helper but never wired into the
        production caller — Claude winging it past CLAUDE.md OTEL
        principle."""

        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_and_confirm(handler)

            evaluated = [
                s
                for s in otel_capture.get_finished_spans()
                if s.name == "chargen.starting_kit_dedup_evaluated"
            ]
            assert len(evaluated) >= 1, (
                "Production chargen-confirm path must emit "
                "chargen.starting_kit_dedup_evaluated. Zero spans means "
                "the helper exists but the call site doesn't wire it — "
                "the exact half-wired failure mode the wire-test guards."
            )
            # The span MUST carry the session identity so the GM panel
            # can attribute the event to a player.
            attrs = dict(evaluated[0].attributes or {})
            assert attrs.get("genre") == "caverns_and_claudes", (
                f"genre attribute must round-trip from session through "
                f"to the span. Got: {attrs.get('genre')!r}."
            )
            assert attrs.get("world") == "grimvault"

        asyncio.run(body())
