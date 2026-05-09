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
    TurnStatusMessage,
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


async def _connect(handler: WebSocketSessionHandler, *, world: str = "caverns_sunden") -> None:
    from tests.server.conftest import attach_default_room_context, seed_slug_for_test

    slug = seed_slug_for_test(handler._save_dir, genre="caverns_and_claudes", world=world)
    attach_default_room_context(handler)
    await handler.handle_message(
        SessionEventMessage(
            payload=SessionEventPayload(
                event="connect",
                player_name="Tester",
                game_slug=slug,
            ),
            player_id="",
        )
    )


def _drain_out_queue(handler: WebSocketSessionHandler) -> list:
    """Drain every message currently on the handler's per-socket out_queue.

    Story 45-26 retargeted these tests onto slug-connect, which attaches a
    SessionRoom + per-socket queue. Post-narration shared-world frames
    (NARRATION_END / PARTY_STATUS / AUDIO_CUE / CHAPTER_MARKER) now route
    through ``room.broadcast`` and land on the per-socket queue rather
    than the per-handler return list — see ``_emit_shared_world_frame``
    in ``websocket_session_handler.py``. Tests that assert the full
    ordered frame stream must combine both sources.
    """
    out_queue = handler._out_queue  # type: ignore[attr-defined]
    drained: list = []
    while not out_queue.empty():
        drained.append(out_queue.get_nowait())
    return drained


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
        # Drain any frames already on the queue from the chargen walk above
        # (e.g. PRESENCE backfill from connect) so the post-confirmation
        # drain captures only the confirmation turn's broadcast frames.
        _drain_out_queue(handler)
        out = await handler.handle_message(
            CharacterCreationMessage(
                payload=CharacterCreationPayload(phase="confirmation"),
                player_id="pid",
            )
        )
    # Concat: per-handler return value + per-socket broadcast queue.
    # Order: the per-handler list reflects the slice the dispatcher writes
    # to its own outbound (CHARACTER_CREATION → PARTY_STATUS{session-start}
    # → cold-open NARRATION → narrator NARRATION); broadcast frames are
    # appended in emit order (NARRATION_END → PARTY_STATUS{post-turn} →
    # AUDIO_CUE) — that's the same player-perceived order the assertions
    # were written against pre-Story-45-26.
    return list(out) + _drain_out_queue(handler)


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

            # Expect 8 frames: CHARACTER_CREATION, PARTY_STATUS (session-
            # start), NARRATION (cold-open seed — the world.yaml opening
            # hook prose, emitted directly to the player so the
            # in-medias-res setup isn't lost as silent narrator prompt-
            # context per playtest 2026-04-25 [P2]), NARRATION (narrator's
            # continuation — same flow, different beat), NARRATION_END,
            # TURN_STATUS{resolved} (ADR-036 sealed-letter pacing — clears
            # the "your turn" banner; fires every narration turn including
            # the opening one), PARTY_STATUS (post-turn refresh carrying
            # current_location landed by the opening narration), AUDIO_CUE
            # (DJ dispatch for the opening narration's mood) — in that
            # order. The first four are returned by the chargen handler;
            # the last four ride the room broadcast queue per
            # ``_emit_shared_world_frame`` (Story 45-26 retarget).
            assert len(out) == 8, [type(m).__name__ for m in out]
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "complete"
            assert isinstance(out[1], PartyStatusMessage)
            assert isinstance(out[2], NarrationMessage)  # cold-open seed
            assert isinstance(out[3], NarrationMessage)  # narrator response
            assert isinstance(out[4], NarrationEndMessage)
            assert isinstance(out[5], TurnStatusMessage)
            assert out[5].payload.status == "resolved"
            assert isinstance(out[6], PartyStatusMessage)
            assert isinstance(out[7], AudioCueMessage)

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

    def test_narration_carries_opening_text(self, handler: WebSocketSessionHandler) -> None:
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
            # Canned-openings flow resolves the opening at chargen-completion
            # (not at connect). Suppress the populate hook so we can
            # simulate "no opening hook" through the full chargen walk.
            import sidequest.server.websocket_session_handler as _wsh

            original_populate = _wsh._populate_opening_directive_on_chargen_complete

            def _no_populate(**_kwargs):
                return None

            try:
                _wsh._populate_opening_directive_on_chargen_complete = _no_populate
                await _connect(handler)
                sd = handler._session_data  # type: ignore[attr-defined]
                sd.opening_seed = None
                sd.opening_directive = None

                out = await _walk_and_confirm(handler)
            finally:
                _wsh._populate_opening_directive_on_chargen_complete = original_populate

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
            # Canned-openings flow: opening directive populates at
            # chargen-completion (in _populate_opening_directive_on_chargen_complete),
            # then is consumed and cleared by _run_opening_turn_narration in
            # the same chargen-confirmation dispatch. Capture the directive
            # the moment it's populated so we can assert its content lands
            # in the rendered prompt below.
            import sidequest.server.websocket_session_handler as _wsh

            captured: dict[str, str | None] = {}
            original_populate = _wsh._populate_opening_directive_on_chargen_complete

            def _capturing_populate(*, session_data, **kw):
                result = original_populate(session_data=session_data, **kw)
                if "directive" not in captured and session_data.opening_directive:
                    captured["seed"] = session_data.opening_seed
                    captured["directive"] = session_data.opening_directive
                return result

            _wsh._populate_opening_directive_on_chargen_complete = _capturing_populate
            try:
                await _connect(handler)
                await _walk_and_confirm(handler)
            finally:
                _wsh._populate_opening_directive_on_chargen_complete = original_populate

            captured_directive = captured.get("directive")
            assert captured_directive is not None, (
                "opening directive was never populated during chargen-completion; "
                "canned-openings flow did not fire"
            )

            # Orchestrator invoked send_with_session at least once for the
            # opening turn. The second positional argument is the rendered
            # prompt string (ClaudeClient.send_with_session(system, prompt, ...)).
            assert claude_mock.send_with_session.called
            call_args = claude_mock.send_with_session.call_args
            # Scan both args and kwargs for the rendered prompt.
            blob = " ".join([*map(str, call_args.args), *map(str, call_args.kwargs.values())])
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
            # Canned-openings flow: seed/directive populate at chargen-
            # completion and are consumed inside the same dispatch. Capture
            # the populate event so we can assert "they were populated then
            # cleared" rather than "populated at connect, cleared after walk".
            import sidequest.server.websocket_session_handler as _wsh

            populated: dict[str, bool] = {"value": False}
            original_populate = _wsh._populate_opening_directive_on_chargen_complete

            def _watching_populate(*, session_data, **kw):
                result = original_populate(session_data=session_data, **kw)
                if session_data.opening_directive is not None:
                    populated["value"] = True
                return result

            _wsh._populate_opening_directive_on_chargen_complete = _watching_populate
            try:
                await _connect(handler)
                sd = handler._session_data  # type: ignore[attr-defined]
                await _walk_and_confirm(handler)
            finally:
                _wsh._populate_opening_directive_on_chargen_complete = original_populate

            assert populated["value"], (
                "opening_directive was never populated during chargen-completion"
            )
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


class TestMPJoinerRaceSuppression:
    """Playtest 2026-04-26 [S2-BUG] coyote_star regression.

    The connect-time MP-joiner suppression in ``_handle_connect``
    (commit afc850a) only fires when the joiner connects AFTER the
    host has completed chargen. In the more common case where both
    players land in the lobby and start chargen together, the joiner
    connects with ``snapshot.characters=[]`` — connect-time
    suppression is a no-op — and at chargen-completion the joiner's
    ``_run_opening_turn_narration`` runs the genre pack's cold-open
    against an already-populated scene.

    Symptom: George (the second player) joining a fresh
    ``space_opera/coyote_star`` MP slug got the ``arena_trial``
    cold-open ("crowd noise hits you like a wall") even though John
    was already at the Trail Junction. The fix must suppress the
    cold-open at consume-time (in ``_run_opening_turn_narration``)
    when the snapshot already has more than this player's PC.
    """

    def test_second_committer_skips_cold_open_seed(
        self,
        handler: WebSocketSessionHandler,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        """Second player to complete chargen on a shared snapshot must
        NOT receive a fresh cold-open NARRATION frame, even when their
        ``sd.opening_seed`` was populated at connect time (race: joiner
        connected before host seated)."""

        async def body() -> None:
            from sidequest.game.character import Character
            from sidequest.game.creature_core import CreatureCore, Inventory

            await _connect(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd is not None
            # Pretend a peer (host) committed first by directly mutating
            # the canonical snapshot — the second-commit branch keys off
            # ``sd.snapshot.characters`` being non-empty before this
            # player's PC is appended. This mirrors the room-shared-
            # snapshot reality at consume-time on the joiner's socket.
            host_core = CreatureCore(
                name="HostPC",
                description="d",
                personality="p",
                inventory=Inventory(),
            )
            host = Character(
                core=host_core,
                char_class="Fighter",
                race="Human",
                backstory="b",
            )
            sd.snapshot.characters.append(host)
            sd.snapshot.player_seats["host-id"] = "HostPC"
            # Note: pre-canned-openings, this point asserted that
            # ``sd.opening_seed`` was already populated at connect-time so
            # the test could prove the consume-time guard catches the race.
            # Canned-openings (2026-05-01) moved opening resolution to
            # chargen-completion, so the seed populates inside
            # ``_walk_and_confirm`` rather than at connect. The consume-time
            # suppression check below still proves the regression — it
            # asserts only the narrator-response NARRATION fires (count==1),
            # not the cold-open seed frame.

            out = await _walk_and_confirm(handler)

            # The cold-open NARRATION must be absent — only the narrator
            # response (framed as a continuation via the fallback action)
            # should appear.
            narrations = [m for m in out if isinstance(m, NarrationMessage)]
            seed_texts = [str(m.payload.text) for m in narrations]
            # The seed prose ("vault's threshold yawns open") would be
            # the cold-open frame — the canned narrator response also
            # contains that phrase, so we discriminate by COUNT:
            # pre-fix: 2 narrations (cold-open + narrator).
            # post-fix: 1 narration (narrator only).
            assert len(narrations) == 1, (
                "MP joiner (snapshot already had a peer character) must "
                "NOT receive a cold-open NARRATION frame; got "
                f"{len(narrations)} narrations: "
                f"{[t[:60] for t in seed_texts]}"
            )

            # OTEL: opening_turn.dispatched must report cold_open_emitted=False
            events = [e for span in otel_capture.get_finished_spans() for e in span.events]
            dispatched = [e for e in events if e.name == "opening_turn.dispatched"]
            assert dispatched, "opening_turn.dispatched span event must fire"
            attrs = dict(dispatched[-1].attributes or {})
            assert attrs["cold_open_emitted"] is False, (
                "Suppressed opening must report cold_open_emitted=False so "
                "the GM panel can see the suppression decision"
            )
            # New OTEL: dedicated suppression event must name pack/world
            # so the GM panel can confirm the fix reaches each pack.
            suppressed = [e for e in events if e.name == "mp_joiner_opening_suppressed_at_consume"]
            assert suppressed, (
                "Consume-time suppression must emit "
                "mp_joiner_opening_suppressed_at_consume span event"
            )
            sup_attrs = dict(suppressed[0].attributes or {})
            assert sup_attrs.get("genre") == "caverns_and_claudes"
            assert sup_attrs.get("world") == "caverns_sunden"

            # Playtest 2026-04-29 BUG-LOW: the suppressed-joiner branch must
            # report ``seed_source="mp_joiner_orientation"`` (not the legacy
            # "fallback" tier — that label hid which dispatch path actually
            # ran from the GM panel). The new tier proves the joiner-aware
            # action string was built (joiner's character name + explicit
            # no-puppeting directive) instead of the generic "I look around
            # and take in my surroundings." that gave the narrator no POV
            # anchor.
            attrs2 = dict(dispatched[-1].attributes or {})
            assert attrs2["seed_source"] == "mp_joiner_orientation", (
                "MP joiner-orientation branch must label its dispatch tier "
                "so the GM panel can verify the fix is firing — got "
                f"seed_source={attrs2['seed_source']!r}"
            )
            # The new action string is longer than the 47-char legacy
            # fallback ("I look around and take in my surroundings."). The
            # bound is loose on purpose — the exact phrasing is allowed to
            # evolve, but the directive is meaningfully longer than the
            # prior generic fallback.
            assert attrs2["action_len"] > 47, (
                "MP joiner-orientation action must be the longer joiner-"
                "aware string, not the generic fallback — got action_len="
                f"{attrs2['action_len']}"
            )

        asyncio.run(body())


class TestOtelEvents:
    def test_opening_turn_otel_events_emitted(
        self, handler: WebSocketSessionHandler, otel_capture: InMemorySpanExporter
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_and_confirm(handler)

            events = [e for span in otel_capture.get_finished_spans() for e in span.events]
            names = {e.name for e in events}
            assert "opening_turn.dispatched" in names
            assert "session.start.character_snapshot_emitted" in names

            dispatched = next(e for e in events if e.name == "opening_turn.dispatched")
            attrs = dict(dispatched.attributes or {})
            assert attrs["has_directive"] is True
            assert attrs["seed_source"] == "world_or_genre_hook"
            assert attrs["genre"] == "caverns_and_claudes"
            assert attrs["world"] == "caverns_sunden"

        asyncio.run(body())


class TestMPJoinerHostLocationAnchor:
    """Playtest 2026-05-02 [BUG-LOW] — opening narration splits party.

    Repro: P1 (Itchy) commits chargen aboard the Kestrel; the
    canned MP opening lands them in the galley. P2 (Charlie)
    commits chargen and the joiner-orientation narrator wandered
    off the established scene, opening Charlie at "Vaskov Centrum
    East Freight Stair" — a different planet entirely. The
    chargen-confirmation epilogue ("the crew is the crew —
    galley, cockpit, the long deck-three spine — the morning is
    yours") promises a shared starting chassis; the narrator
    disagreed because the joiner-orientation prompt did not name
    the host's location.

    Fix: the joiner-orientation action string anchors explicitly
    on ``snapshot.location`` so the narrator cannot relocate the
    second PC to a fresh scene.
    """

    def test_joiner_orientation_carries_host_location_in_prompt(
        self,
        handler: WebSocketSessionHandler,
        claude_mock,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        """When the snapshot already has a host PC AND a non-empty
        ``location``, the joiner's narrator action prompt must
        reference that location verbatim so the narrator cannot
        invent a different scene for the second PC."""

        async def body() -> None:
            from sidequest.game.character import Character
            from sidequest.game.creature_core import CreatureCore, Inventory

            await _connect(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd is not None
            host_core = CreatureCore(
                name="HostPC",
                description="d",
                personality="p",
                inventory=Inventory(),
            )
            host = Character(
                core=host_core,
                char_class="Fighter",
                race="Human",
                backstory="b",
            )
            sd.snapshot.characters.append(host)
            sd.snapshot.player_seats["host-id"] = "HostPC"
            # Simulate the host's prior turn having landed a location —
            # exactly what `narration_apply` does when the host's first
            # turn commits ("snapshot.location = result.location").
            sd.snapshot.character_locations["HostPC"] = "The Kestrel — Galley, Mid-Coast"

            # Reset the mock so we only see the joiner's narrator call
            # (the host's turn never actually fired in this fixture —
            # we just seeded the post-turn snapshot state).
            claude_mock.send_with_session.reset_mock()

            await _walk_and_confirm(handler)

            # Inspect the prompt sent to the narrator. The mock records
            # every send_with_session call with the rendered prompt as
            # the first positional arg.
            calls = claude_mock.send_with_session.call_args_list
            assert calls, (
                "Joiner-orientation must dispatch at least one narrator "
                "turn so we can inspect the prompt"
            )
            # The opening-turn prompt is the FIRST call after reset —
            # subsequent intra-pipeline narrator calls (recap, etc.)
            # may follow but the opening dispatch is first.
            opening_prompt = (
                calls[0].args[0] if calls[0].args else calls[0].kwargs.get("prompt", "")
            )
            assert "The Kestrel — Galley, Mid-Coast" in opening_prompt, (
                "Joiner-orientation prompt must name the host's "
                "location verbatim so the narrator cannot relocate the "
                "second PC. Got prompt fragment: "
                f"{opening_prompt[:600]!r}"
            )
            # And the explicit anti-relocation directive must be present.
            assert "Do NOT relocate them to a new location" in opening_prompt, (
                "Joiner-orientation prompt must carry the explicit no-relocation directive"
            )

            # OTEL: the dedicated anchor event must fire so the GM panel
            # can see which path the joiner-orientation took
            # (CLAUDE.md OTEL principle).
            events = [e for span in otel_capture.get_finished_spans() for e in span.events]
            anchored = [e for e in events if e.name == "mp_joiner_orientation_anchored"]
            assert anchored, (
                "mp_joiner_orientation_anchored watcher event must fire "
                "so the GM panel sees the anchor decision"
            )
            attrs = dict(anchored[0].attributes or {})
            assert attrs.get("anchor_kind") == "host_location"
            assert attrs.get("host_location") == "The Kestrel — Galley, Mid-Coast"

        asyncio.run(body())

    def test_joiner_orientation_falls_back_when_no_host_location(
        self,
        handler: WebSocketSessionHandler,
        claude_mock,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        """Defensive path: if the host's narration somehow hasn't set
        ``snapshot.location`` yet (very early MP race), the joiner
        prompt falls back to a same-scene clause and emits the
        ``fallback_same_scene`` anchor kind for GM-panel visibility.
        """

        async def body() -> None:
            from sidequest.game.character import Character
            from sidequest.game.creature_core import CreatureCore, Inventory

            await _connect(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd is not None
            host_core = CreatureCore(
                name="HostPC",
                description="d",
                personality="p",
                inventory=Inventory(),
            )
            host = Character(
                core=host_core,
                char_class="Fighter",
                race="Human",
                backstory="b",
            )
            sd.snapshot.characters.append(host)
            sd.snapshot.player_seats["host-id"] = "HostPC"
            # Wave 2B: simulate no host narration yet — leave the host's
            # character_locations entry absent.
            sd.snapshot.character_locations.pop("HostPC", None)

            claude_mock.send_with_session.reset_mock()
            await _walk_and_confirm(handler)

            events = [e for span in otel_capture.get_finished_spans() for e in span.events]
            anchored = [e for e in events if e.name == "mp_joiner_orientation_anchored"]
            assert anchored, "anchor watcher event must fire on every joiner-orientation"
            attrs = dict(anchored[0].attributes or {})
            assert attrs.get("anchor_kind") == "fallback_same_scene"

        asyncio.run(body())
