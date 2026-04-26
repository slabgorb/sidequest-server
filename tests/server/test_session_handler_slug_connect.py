"""Tests for WebSocket connect accepting game_slug (Task 4, MP-01).

Verifies that SESSION_EVENT{connect} with a game_slug field:
- loads the game from the slug-based SQLite store and emits SESSION_CONNECTED
- emits ERROR when the slug doesn't correspond to a known game
- genre_pack is populated (not None) so PLAYER_ACTION doesn't crash
- a slug-connect for a session with a saved snapshot resumes rather than restarting
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sidequest.game.persistence import GameMode, SqliteStore, db_path_for_slug, upsert_game
from sidequest.game.session import GameSnapshot
from sidequest.protocol.messages import (
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.session_room import RoomRegistry


def _make_handler(save_dir: Path, search_paths: list[Path]) -> WebSocketSessionHandler:
    """Construct a handler with room-context wiring.

    Mirrors what ws_endpoint does: build the handler, then immediately call
    attach_room_context with a fresh RoomRegistry, a unique socket_id, and an
    asyncio.Queue for outbound messages. The slug-connect branch requires
    all three — there is no silent test-only bypass.
    """
    handler = WebSocketSessionHandler(
        save_dir=save_dir,
        genre_pack_search_paths=search_paths,
    )
    handler.attach_room_context(
        registry=RoomRegistry(),
        socket_id="sock-test",
        out_queue=asyncio.Queue(),
    )
    return handler

# Use a genre pack that exists in the content repo.
_GENRE = "caverns_and_claudes"
_WORLD = "grimvault"
_SLUG = "2026-04-22-grimvault-test"

# Resolve the content search path relative to this file so tests work from
# any working directory.
# __file__ = oq-2/sidequest-server/tests/server/<file>.py
# parents[3] = oq-2 (orchestrator root)
_CONTENT_SEARCH_PATH = (
    Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
)


@pytest.fixture
def seeded_game(tmp_path: Path) -> Path:
    slug = _SLUG
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)
    store.close()
    return tmp_path


@pytest.mark.asyncio
async def test_connect_by_slug_loads_existing_game(seeded_game: Path):
    handler = _make_handler(seeded_game, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug=_SLUG,
        ),
    )
    outbound = await handler.handle_message(msg)
    assert any(getattr(m, "type", None) == "SESSION_EVENT" for m in outbound), (
        f"Expected SESSION_EVENT(connected) in outbound, got: {[getattr(m, 'type', None) for m in outbound]}"
    )
    # Verify the connected event has event="connected"
    connected_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "connected"
    ]
    assert connected_msgs, (
        f"Expected SESSION_EVENT{{connected}} in outbound, got: {outbound}"
    )
    assert handler.session_data is not None
    assert handler.session_data.game_slug == _SLUG
    assert handler.session_data.mode == GameMode.MULTIPLAYER
    # Bug 1 regression: genre_pack must be a real GenrePack, never None.
    assert handler.session_data.genre_pack is not None, (
        "genre_pack must not be None after slug-connect — PLAYER_ACTION would crash"
    )


@pytest.mark.asyncio
async def test_connect_by_unknown_slug_errors(seeded_game: Path):
    handler = _make_handler(seeded_game, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug="2020-01-01-nowhere",
        ),
    )
    outbound = await handler.handle_message(msg)
    assert any(getattr(m, "type", None) == "ERROR" for m in outbound), (
        f"Expected ERROR in outbound, got: {[getattr(m, 'type', None) for m in outbound]}"
    )


@pytest.mark.asyncio
async def test_slug_connect_resumes_saved_snapshot(tmp_path: Path):
    """Bug 2 regression: slug-connect with a saved session restores it.

    Seeds a game row *and* a saved GameSnapshot with one character so
    has_character comes back True and state is Playing (not Creating).
    """
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    slug = "2026-04-22-resume-test"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)

    # Build a minimal character and save a snapshot that contains it.
    core = CreatureCore(
        name="Rux",
        description="A stoic fighter",
        personality="stoic",
        inventory=Inventory(),
    )
    char = Character(core=core, char_class="Fighter", race="Human", backstory="A wandering fighter")
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD, location="Entrance")
    snap.characters = [char]
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()

    handler = _make_handler(tmp_path, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        # Original player resuming: UI sends display_name as both player_id
        # and player_name. The MP-legacy-backfill branch will match
        # display_name "Rux" against the existing character "Rux" and
        # back-fill player_seats so subsequent joiners see the populated
        # seat map. See test_mp_legacy_save_resumes_original_player_by_name
        # for the explicit branch assertion.
        player_id="Rux",
        payload=SessionEventPayload(
            event="connect", game_slug=slug, player_name="Rux",
        ),
    )
    outbound = await handler.handle_message(msg)

    connected_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "connected"
    ]
    assert connected_msgs, f"Expected SESSION_EVENT(connected), got: {outbound}"

    connected_payload = connected_msgs[0].payload
    # has_character must reflect the saved snapshot, not hardcoded False.
    assert connected_payload.has_character is True, (
        "has_character should be True when the saved snapshot has a character"
    )
    sd = handler.session_data
    assert sd is not None
    assert sd.snapshot.characters, "Snapshot must carry the saved character after resume"
    assert sd.snapshot.characters[0].core.name == "Rux"


@pytest.mark.asyncio
async def test_slug_connect_emits_mp_span(seeded_game: Path):
    """Wiring test: mp.slug_connect span fires with the expected attrs.

    Per CLAUDE.md OTEL mandate — GM panel must be able to tell that
    slug-connect actually ran (vs. Claude improvising a plausible-looking
    narration). This test is the lie-detector for that claim.
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
        handler = _make_handler(seeded_game, [_CONTENT_SEARCH_PATH])
        msg = SessionEventMessage(
            type="SESSION_EVENT",
            player_id="alice",
            payload=SessionEventPayload(event="connect", game_slug=_SLUG),
        )
        await handler.handle_message(msg)

        mp_spans = [s for s in exporter.get_finished_spans() if s.name == "mp.slug_connect"]
        assert len(mp_spans) == 1, (
            f"Expected exactly one mp.slug_connect span, got {[s.name for s in exporter.get_finished_spans()]}"
        )
        span = mp_spans[0]
        assert span.attributes["slug"] == _SLUG
        assert span.attributes["player_id"] == "alice"
        # row.mode is GameMode.MULTIPLAYER → "multiplayer"
        assert span.attributes["mode"] == "multiplayer"
        # Fresh connect — no one was paused before.
        assert span.attributes["was_paused_before"] is False
        assert span.attributes["resolved_pause"] is False
        assert span.attributes["connected_count"] == 1
    finally:
        processor.shutdown()


@pytest.mark.asyncio
async def test_slug_connect_routes_new_player_to_chargen_when_seat_taken(tmp_path: Path):
    """Per-player chargen gate (playtest 2026-04-25 bug 1).

    A snapshot with ``player_seats={"P1": "Laverne"}`` already seats Player 1.
    When Player 2 (a *new* player_id) connects to the same slug, the gate
    must report ``has_character is False`` so the UI routes to chargen
    instead of auto-claiming Laverne.

    This is the wiring test OQ-2 flagged as missing — without it, a future
    refactor could regress the gate to ``bool(snapshot.characters)`` and
    no test would catch it.
    """
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    slug = "2026-04-25-multiseat-test"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)

    core = CreatureCore(
        name="Laverne",
        description="A blunt delver",
        personality="brusque",
        inventory=Inventory(),
    )
    char = Character(core=core, char_class="Fighter", race="Human", backstory="P1's PC")
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD, location="Entrance")
    snap.characters = [char]
    snap.player_seats = {"P1": "Laverne"}
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()

    handler = _make_handler(tmp_path, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="P2",
        payload=SessionEventPayload(event="connect", game_slug=slug),
    )
    outbound = await handler.handle_message(msg)

    connected_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "connected"
    ]
    assert connected_msgs, f"Expected SESSION_EVENT(connected), got: {outbound}"
    payload = connected_msgs[0].payload
    assert payload.has_character is False, (
        "P2 (new player_id) must NOT inherit P1's seat — gate must report "
        "has_character=False so the UI routes to chargen"
    )


@pytest.mark.asyncio
async def test_slug_connect_resumes_seated_player_by_id(tmp_path: Path):
    """Same-player_id resume keeps the gate at has_character=True.

    Companion to the new-player-routes-to-chargen test above. P1 reconnecting
    to a slug where ``player_seats`` already binds them must resume their PC.
    """
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    slug = "2026-04-25-resume-seated-test"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)

    core = CreatureCore(
        name="Laverne",
        description="A blunt delver",
        personality="brusque",
        inventory=Inventory(),
    )
    char = Character(core=core, char_class="Fighter", race="Human", backstory="P1's PC")
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD, location="Entrance")
    snap.characters = [char]
    snap.player_seats = {"P1": "Laverne"}
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()

    handler = _make_handler(tmp_path, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="P1",
        payload=SessionEventPayload(event="connect", game_slug=slug),
    )
    outbound = await handler.handle_message(msg)

    connected_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "connected"
    ]
    assert connected_msgs, f"Expected SESSION_EVENT(connected), got: {outbound}"
    payload = connected_msgs[0].payload
    assert payload.has_character is True, (
        "P1 reconnecting to their own seat must resume — gate reports "
        "has_character=True"
    )


@pytest.mark.asyncio
async def test_slug_connect_chargen_gate_logs_branch_decision(
    tmp_path: Path, caplog
):
    """OTEL mandate (CLAUDE.md): the chargen-gate decision must be loggable.

    Without this log line, GM panel can't tell whether the per-player gate
    actually ran or whether the snapshot just happened to be empty. That's
    a CLAUDE.md OTEL Observability Principle violation.
    """
    import logging

    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    slug = "2026-04-25-gate-log-test"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)

    core = CreatureCore(
        name="Laverne", description="d", personality="p", inventory=Inventory(),
    )
    char = Character(core=core, char_class="Fighter", race="Human", backstory="b")
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD, location="Entrance")
    snap.characters = [char]
    snap.player_seats = {"P1": "Laverne"}
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()

    handler = _make_handler(tmp_path, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="P2",
        payload=SessionEventPayload(event="connect", game_slug=slug),
    )
    with caplog.at_level(logging.INFO, logger="sidequest.server.session_handler"):
        await handler.handle_message(msg)

    gate_records = [
        r for r in caplog.records
        if "session.chargen_gate" in r.getMessage()
    ]
    assert gate_records, (
        "Chargen-gate must emit an info-level log line so GM panel can verify "
        "which branch fired"
    )
    msg_text = gate_records[0].getMessage()
    assert "branch=player_seats" in msg_text, (
        f"With populated player_seats, branch=player_seats expected; got: {msg_text}"
    )
    assert "has_character=False" in msg_text, (
        f"P2 connecting to a slug seated by P1 must log has_character=False; got: {msg_text}"
    )


@pytest.mark.asyncio
async def test_mp_legacy_save_routes_new_joiner_to_chargen(tmp_path: Path, caplog):
    """Playtest 2026-04-25 P0: MP slug with characters but empty player_seats.

    Pre-fix: when Laverne's chargen completed on a server BEFORE the
    chargen-confirmation seat-binding landed, her save has
    ``characters=[Laverne]`` but ``player_seats={}``. The legacy fallback
    branch read ``has_character = bool(snapshot.characters)`` and routed
    ANY connecting player straight to slug_resume — Player 2 connected
    with name "Squiggy" and landed on Laverne's character sheet labeled
    "(YOU)".

    Post-fix: in MP mode with empty player_seats, the gate matches
    ``display_name`` against existing character names. Squiggy doesn't
    match → has_character=False → routed to chargen, plus a watcher
    event ``mp_new_joiner_chargen_required`` for the GM panel.
    """
    import logging

    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    slug = "2026-04-25-mp-legacy-no-seats"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)

    core = CreatureCore(
        name="Laverne", description="d", personality="p", inventory=Inventory(),
    )
    char = Character(core=core, char_class="Fighter", race="Human", backstory="b")
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD, location="Entrance")
    snap.characters = [char]
    # Crucial: empty player_seats simulates pre-binding chargen save.
    snap.player_seats = {}
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()

    handler = _make_handler(tmp_path, [_CONTENT_SEARCH_PATH])
    # Squiggy connects with their own display name (UI sends displayName as
    # both player_id and player_name on slug-connect).
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="Squiggy",
        payload=SessionEventPayload(
            event="connect", game_slug=slug, player_name="Squiggy",
        ),
    )
    with caplog.at_level(logging.INFO, logger="sidequest.server.session_handler"):
        outbound = await handler.handle_message(msg)

    connected_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "connected"
    ]
    assert connected_msgs, f"Expected SESSION_EVENT(connected), got: {outbound}"
    payload = connected_msgs[0].payload
    assert payload.has_character is False, (
        "Squiggy (new joiner, MP, empty seats) must NOT inherit Laverne's "
        "character — gate must report has_character=False so the UI routes "
        "to chargen"
    )
    # ``ready`` events mean slug_resume fired — the bug. There must be NO
    # SESSION_EVENT(ready) on this connect.
    ready_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "ready"
    ]
    assert not ready_msgs, (
        f"Squiggy must NOT receive SESSION_EVENT(ready) (slug_resume) — "
        f"chargen fork required. Got: {ready_msgs}"
    )
    # Gate-decision log must show the new MP-aware branch fired.
    gate_records = [
        r for r in caplog.records
        if "session.chargen_gate" in r.getMessage()
    ]
    assert gate_records, "Chargen-gate must log its decision"
    msg_text = gate_records[0].getMessage()
    assert "branch=mp_new_joiner_chargen_required" in msg_text, (
        f"Expected MP-new-joiner branch; got: {msg_text}"
    )


@pytest.mark.asyncio
async def test_mp_legacy_save_resumes_original_player_by_name(tmp_path: Path, caplog):
    """Companion to the new-joiner test: original player CAN resume.

    Laverne reconnects to her own pre-binding save. ``display_name=Laverne``
    matches the existing character → has_character=True → resume, AND the
    seat is back-filled so subsequent joiners hit the authoritative
    ``player_seats`` branch.
    """
    import logging

    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    slug = "2026-04-25-mp-legacy-backfill"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)

    core = CreatureCore(
        name="Laverne", description="d", personality="p", inventory=Inventory(),
    )
    char = Character(core=core, char_class="Fighter", race="Human", backstory="b")
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD, location="Entrance")
    snap.characters = [char]
    snap.player_seats = {}
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()

    handler = _make_handler(tmp_path, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="Laverne",
        payload=SessionEventPayload(
            event="connect", game_slug=slug, player_name="Laverne",
        ),
    )
    with caplog.at_level(logging.INFO, logger="sidequest.server.session_handler"):
        outbound = await handler.handle_message(msg)

    connected_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "connected"
    ]
    assert connected_msgs
    assert connected_msgs[0].payload.has_character is True
    # Branch log must show backfill.
    gate_records = [
        r for r in caplog.records
        if "session.chargen_gate" in r.getMessage()
    ]
    assert gate_records
    assert "branch=mp_legacy_backfill" in gate_records[0].getMessage()


@pytest.mark.asyncio
async def test_mp_joiner_suppresses_opening_seed(tmp_path: Path, caplog):
    """Wiring test: MP joiner does NOT inherit the cold-open hook.

    Playtest 2026-04-26 "Multiplayer parallel-solo desynchronizes scene
    context entirely". When Player 2 joins a slug that already has a
    seated character, completing chargen used to trigger
    ``_run_opening_turn_narration`` with the genre pack's
    in-medias-res ``opening_seed`` + ``opening_directive``. The narrator
    obeyed the directive and invented a NEW scene divorced from where
    Player 1 already was — Ralph stuck at the Sinkhole Inn while Potsie
    descended into "THE THROAT".

    Post-fix: when ``has_character=False`` AND the snapshot already has
    >=1 character, the connect handler suppresses ``opening_seed`` and
    ``opening_directive`` on the new joiner's session, and emits the
    ``mp_joiner_opening_suppressed`` watcher event so the GM panel can
    confirm the suppression. The post-chargen narration falls back to
    the generic "I look around…" action which the shared narrator
    (ADR-067) handles as a continuation of the existing scene.
    """
    import logging

    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    slug = "2026-04-26-mp-joiner-opening-suppressed"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)

    # Seat Ralph as the existing character so Potsie joins an in-progress
    # world. player_seats populated → the gate's ``player_seats`` branch
    # fires (Potsie absent → has_character=False), which is the canonical
    # MP-joiner path post-MP-02.
    core = CreatureCore(
        name="Ralph", description="d", personality="p", inventory=Inventory(),
    )
    char = Character(core=core, char_class="Fighter", race="Human", backstory="b")
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD, location="Sinkhole Inn Room")
    snap.characters = [char]
    snap.player_seats = {"ralph-id": "Ralph"}
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()

    handler = _make_handler(tmp_path, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="potsie-id",
        payload=SessionEventPayload(
            event="connect", game_slug=slug, player_name="Potsie",
        ),
    )
    with caplog.at_level(logging.INFO, logger="sidequest.server.session_handler"):
        await handler.handle_message(msg)

    # The session-data we just constructed must have None for both opening
    # fields — that's the actual state the post-chargen turn will read.
    sd = handler._session_data
    assert sd is not None, "Handler must have built _session_data on connect"
    assert sd.opening_seed is None, (
        "MP joiner must NOT inherit the genre pack's opening_seed; got: "
        f"{sd.opening_seed!r}"
    )
    assert sd.opening_directive is None, (
        "MP joiner must NOT inherit the genre pack's opening_directive; got: "
        f"{sd.opening_directive!r}"
    )
    # Suppression decision must be logged so a future drift can be diagnosed
    # from logs alone (not just from missing scenes).
    suppress_records = [
        r for r in caplog.records
        if "session.mp_joiner_opening_suppressed" in r.getMessage()
    ]
    assert suppress_records, (
        "Suppression must log session.mp_joiner_opening_suppressed for "
        "GM-panel observability"
    )


@pytest.mark.asyncio
async def test_slug_connect_without_room_context_raises(seeded_game: Path):
    """Wiring test: slug-connect must fail loudly when attach_room_context was skipped.

    Regression test for the removed `hasattr(self, "_room_registry")` silent
    fallback. Any code path that reaches slug-connect without the WebSocket
    lifecycle having called attach_room_context() is a wiring bug — the
    handler must refuse to proceed, not silently skip room registration.
    """
    handler = WebSocketSessionHandler(
        save_dir=seeded_game,
        genre_pack_search_paths=[_CONTENT_SEARCH_PATH],
    )
    # Deliberately do NOT call attach_room_context.
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(event="connect", game_slug=_SLUG),
    )
    with pytest.raises(RuntimeError, match="attach_room_context"):
        await handler.handle_message(msg)
