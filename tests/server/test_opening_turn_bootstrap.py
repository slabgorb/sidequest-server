"""Opening-turn bootstrap integration — Story 2.3 Slice H.

Drives chargen confirmation against caverns_and_claudes/grimvault and
asserts the combined response after commit:

- ``CHARACTER_CREATION{phase=complete}`` — the commit frame (Slice D-G)
- ``PARTY_STATUS`` with a populated :class:`CharacterSheetDetails`
  (race / stats / abilities / personality) so the client Character
  tab lands populated at session-start
- ``NARRATION`` + ``NARRATION_END`` — the opening turn fired through
  the orchestrator using ``opening_seed`` and ``opening_directive``
  resolved at connect (Slice B)

Additionally verifies ``opening_directive`` makes it into the
narrator prompt (Early zone), and both the seed + directive are
zeroed on ``_SessionData`` after consumption so subsequent
PLAYER_ACTION turns run directive-free.
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
    AudioCueMessage,
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    NarrationEndMessage,
    NarrationMessage,
    PartyStatusMessage,
    PlayerActionMessage,
    PlayerActionPayload,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from tests.server.conftest import make_mock_claude_client

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"

CANNED_OPENING_TEXT = (
    "The vault's threshold yawns open before you, cool air rising from the "
    "stone. Whatever waits below has waited long.\n\n"
    "```game_patch\n{}\n```"
)


@pytest.fixture
def claude_mock():
    return make_mock_claude_client(text=CANNED_OPENING_TEXT, session_id="opening-001")


@pytest.fixture
def handler(tmp_path: Path, claude_mock) -> WebSocketSessionHandler:
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("content pack not found")
    return WebSocketSessionHandler(
        claude_client_factory=lambda: claude_mock,
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


async def _connect(
    handler: WebSocketSessionHandler, *, world: str = "grimvault"
) -> None:
    await handler.handle_message(
        SessionEventMessage(
            payload=SessionEventPayload(
                event="connect",
                player_name="Tester",
                genre="caverns_and_claudes",
                world=world,
            ),
            player_id="",
        )
    )


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


def _by_type(messages: list) -> dict[type, list]:
    grouped: dict[type, list] = {}
    for m in messages:
        grouped.setdefault(type(m), []).append(m)
    return grouped


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpeningTurnFrames:
    def test_confirmation_emits_complete_party_status_and_narration(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            out = await _walk_and_confirm(handler)

            # Expect 7 frames: CHARACTER_CREATION, PARTY_STATUS (session-
            # start), NARRATION (cold-open seed — the world.yaml opening
            # hook prose, emitted directly to the player so the
            # in-medias-res setup isn't lost as silent narrator prompt-
            # context per playtest 2026-04-25 [P2]), NARRATION (narrator's
            # continuation — same flow, different beat), NARRATION_END,
            # PARTY_STATUS (post-turn refresh carrying current_location
            # landed by the opening narration), AUDIO_CUE (DJ dispatch
            # for the opening narration's mood) — in that order.
            assert len(out) == 7, [type(m).__name__ for m in out]
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"
            assert isinstance(out[1], PartyStatusMessage)
            assert isinstance(out[2], NarrationMessage)  # cold-open seed
            assert isinstance(out[3], NarrationMessage)  # narrator response
            assert isinstance(out[4], NarrationEndMessage)
            assert isinstance(out[5], PartyStatusMessage)
            assert isinstance(out[6], AudioCueMessage)

        asyncio.run(body())

    def test_party_status_carries_full_character_sheet(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            out = await _walk_and_confirm(handler)

            ps = next(m for m in out if isinstance(m, PartyStatusMessage))
            assert len(ps.payload.members) == 1
            member = ps.payload.members[0]
            assert member.character_name is not None
            assert str(member.character_name) == "Tester"
            assert str(member.class_) == "Delver"
            assert member.sheet is not None
            assert str(member.sheet.race) == "Human"
            assert member.sheet.stats  # non-empty dict
            # Caverns Delver loadout pulls equipment into inventory.
            assert member.inventory is not None
            assert len(member.inventory.items) > 0

        asyncio.run(body())

    def test_narration_carries_opening_text(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            out = await _walk_and_confirm(handler)

            narrations = [m for m in out if isinstance(m, NarrationMessage)]
            # Two NARRATION frames: the cold-open seed first, then the
            # narrator's continuation. Both carry text the player reads.
            assert len(narrations) == 2
            cold_open_text = str(narrations[0].payload.text)
            narrator_text = str(narrations[1].payload.text)
            # Cold open is the world's first_turn_seed — non-empty prose
            # the world author wrote (real grimvault content, not the
            # canned narrator response).
            assert cold_open_text  # non-blank
            assert "vault's threshold" not in cold_open_text  # ≠ narrator
            # Narrator continuation echoes the canned response.
            assert "vault's threshold" in narrator_text

        asyncio.run(body())

    def test_cold_open_emitted_only_when_opening_seed_present(
        self, handler: WebSocketSessionHandler
    ) -> None:
        """Regression: when the pack has no opening hook (sd.opening_seed
        is None), the cold-open NARRATION frame must NOT fire. Otherwise
        the fallback prompt ("I look around and take in my surroundings.")
        would leak as player-facing prose, when it's actually the
        engine's implicit action.
        """
        async def body() -> None:
            await _connect(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            sd.opening_seed = None
            sd.opening_directive = None

            out = await _walk_and_confirm(handler)
            narrations = [m for m in out if isinstance(m, NarrationMessage)]
            # Without a seed, only the narrator's response narration fires.
            assert len(narrations) == 1
            assert "vault's threshold" in str(narrations[0].payload.text)

        asyncio.run(body())


class TestOpeningDirectiveInjection:
    def test_opening_directive_lands_in_prompt(
        self, handler: WebSocketSessionHandler, claude_mock
    ) -> None:
        """Connect resolves an opening hook (Slice B), which renders the
        directive onto ``_SessionData``. At confirmation, ``_run_opening_turn``
        builds a TurnContext with the directive set, the orchestrator
        registers it in the Early zone, and the rendered prompt sent to
        Claude must include the directive text.

        The conftest mock sits on the ClaudeClient's ``send_with_session``
        so inspecting ``call_args`` reveals the prompt that was built."""

        async def body() -> None:
            await _connect(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            # Sanity: opening hook was resolved at connect.
            assert sd.opening_seed is not None
            assert sd.opening_directive is not None
            captured_directive = sd.opening_directive

            await _walk_and_confirm(handler)

            # Orchestrator invoked send_with_session at least once for the
            # opening turn. The second positional argument is the rendered
            # prompt string (ClaudeClient.send_with_session(system, prompt, ...)).
            assert claude_mock.send_with_session.called
            call_args = claude_mock.send_with_session.call_args
            # Scan both args and kwargs for the rendered prompt.
            blob = " ".join(
                [*map(str, call_args.args), *map(str, call_args.kwargs.values())]
            )
            # The directive must have been injected into the prompt.
            # Substring match on a stable phrase from the directive keeps
            # this resilient to template tweaks around the edges.
            assert captured_directive.split("\n")[0][:30] in blob, (
                f"opening directive first-line not found in narrator prompt:\n"
                f"directive={captured_directive!r}\nblob_snippet={blob[:800]!r}"
            )

        asyncio.run(body())

    def test_seed_and_directive_cleared_after_opening_turn(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd.opening_seed is not None
            assert sd.opening_directive is not None

            await _walk_and_confirm(handler)

            assert sd.opening_seed is None
            assert sd.opening_directive is None

        asyncio.run(body())

    def test_subsequent_player_action_has_no_directive(
        self, handler: WebSocketSessionHandler, claude_mock
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_and_confirm(handler)
            # Reset the call history so we can isolate the post-opening turn.
            claude_mock.send_with_session.reset_mock()

            # Fire a regular PLAYER_ACTION. The directive was consumed by
            # the opening turn; the prompt here should carry no directive.
            await handler.handle_message(
                PlayerActionMessage(
                    payload=PlayerActionPayload(
                        action="I step through the threshold.",
                    ),
                    player_id="pid",
                )
            )
            # Session-level directive stays cleared across the next turn.
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd.opening_directive is None
            assert sd.opening_seed is None

        asyncio.run(body())


class TestOtelEvents:
    def test_opening_turn_otel_events_emitted(
        self, handler: WebSocketSessionHandler, otel_capture: InMemorySpanExporter
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_and_confirm(handler)

            events = [
                e
                for span in otel_capture.get_finished_spans()
                for e in span.events
            ]
            names = {e.name for e in events}
            assert "opening_turn.dispatched" in names
            assert "session.start.character_snapshot_emitted" in names

            dispatched = next(e for e in events if e.name == "opening_turn.dispatched")
            attrs = dict(dispatched.attributes or {})
            assert attrs["has_directive"] is True
            assert attrs["seed_source"] == "world_or_genre_hook"
            assert attrs["genre"] == "caverns_and_claudes"
            assert attrs["world"] == "grimvault"

        asyncio.run(body())
