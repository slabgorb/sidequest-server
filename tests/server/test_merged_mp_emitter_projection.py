"""ADR-105 Track A — merged-MP emitter must not be perception-bypassed.

The MP fan-out (`sidequest/server/emitters.py:emit_event`) excludes the
*emitter* (`handler._session_data.player_id`) from per-recipient
projection — they receive the raw Invariant-3 frame. That is correct in
**solo** (the single player is the sole author) but catastrophic in
**merged-MP dispatch**: the driving handler is whichever player submitted
last, NOT the sole author of a shared narration covering every seated PC.
The driver is then the one player who never gets a
`projection.filter.decide` (×0 spans) and receives the unfiltered shared
blob — the confirmed 2026-05-16 caverns_sunden information-firewall
breach (ADR-105).

Track A makes the call site thread an explicit ``author_player_id``. When
set, the driver is projected like any other recipient (one
`projection.filter.decide` per DISTINCT connected player, swap target ==
that recipient). When ``None`` (solo / legacy callers), the deliberate
emitter-bypass + lazy_fill-on-reconnect invariant is preserved
byte-identical (guarded by
``test_projection_end_to_end_wiring.test_emitter_reconnect_relies_on_lazy_fill``).

Content redaction of the shared blob is explicitly Track B — this test
asserts *binding* (one projection pass per distinct recipient incl. the
driver), not content.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.event_log import EventLog
from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.projection.cache import ProjectionCache
from sidequest.game.projection.composed import ComposedFilter
from sidequest.game.projection.rules import load_rules_from_yaml_str
from sidequest.game.session import GameSnapshot
from sidequest.server.session_handler import WebSocketSessionHandler, _SessionData
from sidequest.server.session_room import RoomRegistry

_GENRE = "caverns_and_claudes"
_WORLD = "sunden"
_SLUG = "merged-mp-emitter-projection"
_FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "packs"

# Mirrors caverns_and_claudes/projection.yaml — NARRATION is visibility_tag
# gated. With visible_to:"all" (Track B not yet landed) every player is
# INCLUDED; this test asserts the *projection pass* fires per recipient,
# not that content is redacted.
_RULES_YAML = """
rules:
  - kind: NARRATION
    visibility_tag: {}
"""


def _pc(name: str, pronouns: str = "he/him") -> Character:
    core = CreatureCore(
        name=name,
        description="A test subject.",
        personality="Test.",
        inventory=Inventory(),
    )
    return Character(
        core=core,
        backstory="A wanderer.",
        char_class="Fighter",
        race="Human",
        pronouns=pronouns,
    )


def _setup_tracing() -> InMemorySpanExporter:
    """Attach an in-memory exporter to the active TracerProvider.

    OTEL forbids replacing a provider once set, so attach a processor to
    the existing one when present (mirrors
    test_projection_end_to_end_wiring._setup_tracing).
    """
    exporter = InMemorySpanExporter()
    current = trace.get_tracer_provider()
    if hasattr(current, "add_span_processor"):
        current.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    return exporter


def _seed_game_row(tmp_path: Path) -> SqliteStore:
    db = db_path_for_slug(tmp_path, _SLUG)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=_SLUG,
        mode=GameMode.MULTIPLAYER,
        genre_slug=_GENRE,
        world_slug=_WORLD,
    )
    return store


def _make_handler_three_pcs(tmp_path: Path) -> WebSocketSessionHandler:
    """Three connected/seated PCs. ``p_carl`` is the driving handler's
    own player (``_session_data.player_id``) — i.e. the merged-dispatch
    last-submitter / emitter."""
    handler = WebSocketSessionHandler(save_dir=tmp_path, genre_pack_search_paths=[_FIXTURE_PACKS])
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD)
    snap.characters = [
        _pc("Carl", pronouns="he/him"),
        _pc("Donut", pronouns="he/him"),
        _pc("Katia", pronouns="she/her"),
    ]
    handler._session_data = _SessionData.__new__(_SessionData)
    handler._session_data.snapshot = snap
    handler._session_data.player_id = "p_carl"  # driver / emitter
    handler._session_data.genre_slug = _GENRE
    handler._session_data.world_slug = _WORLD

    store = _seed_game_row(tmp_path)
    handler._event_log = EventLog(store)
    handler._projection_filter = ComposedFilter(
        rules=load_rules_from_yaml_str(_RULES_YAML),
        pack_slug=_GENRE,
    )
    handler._projection_cache = ProjectionCache(store)

    registry = RoomRegistry()
    room = registry.get_or_create(slug=_SLUG, mode=GameMode.MULTIPLAYER)
    room.connect("p_carl", socket_id="sock-carl")
    room.connect("p_donut", socket_id="sock-donut")
    room.connect("p_katia", socket_id="sock-katia")
    room.seat("p_carl", character_slot="Carl")
    room.seat("p_donut", character_slot="Donut")
    room.seat("p_katia", character_slot="Katia")
    handler._room = room
    return handler


def _attach_queues(room) -> dict[str, asyncio.Queue]:
    qs = {pid: asyncio.Queue() for pid in ("p_carl", "p_donut", "p_katia")}
    room.attach_outbound("sock-carl", qs["p_carl"])
    room.attach_outbound("sock-donut", qs["p_donut"])
    room.attach_outbound("sock-katia", qs["p_katia"])
    return qs


def _decide_player_ids(exporter: InMemorySpanExporter) -> set[str]:
    return {
        (s.attributes or {}).get("player_id", "")
        for s in exporter.get_finished_spans()
        if s.name == "projection.filter.decide"
    }


_PAYLOAD = {
    "text": "Carl plants a boot on the moth's thorax while the others watch.",
    "footnotes": [],
    "_visibility": {
        "visible_to": "all",
        "fidelity": {},
        "anchor_pc": "Carl",
        "pov_strategy": "pc_anchored",
    },
}


def test_merged_mp_threads_author_projects_every_distinct_recipient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``author_player_id`` is threaded (merged-MP shared turn),
    ``projection.filter.decide`` must fire once per DISTINCT connected
    player INCLUDING the driving/emitter player.

    Pre-Track-A: the emitter (p_carl) is raw-bypassed → ZERO decide
    spans for them; only p_donut/p_katia are projected. This is the
    exact Jaeger ×0-for-the-driver signature of the firewall breach.
    """
    handler = _make_handler_three_pcs(tmp_path)
    _attach_queues(handler._room)

    from sidequest.server import session_handler as handler_module
    from sidequest.server import views as views_module

    class _FakeMsg:
        def __init__(self, payload):
            self.payload = payload

    monkeypatch.setitem(handler_module._KIND_TO_MESSAGE_CLS, "NARRATION", _FakeMsg)
    monkeypatch.setattr(views_module, "status_effects_by_player", lambda _h: {})

    exporter = _setup_tracing()
    exporter.clear()

    handler._emit_event("NARRATION", dict(_PAYLOAD), author_player_id="p_carl")

    assert _decide_player_ids(exporter) == {"p_carl", "p_donut", "p_katia"}, (
        "merged-MP: every DISTINCT connected player (incl. the driving "
        "emitter p_carl) must get exactly one projection.filter.decide; "
        "the driver being absent is the ADR-105 firewall breach"
    )


def test_solo_legacy_emitter_bypass_preserved_when_no_author(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: with no ``author_player_id`` (solo / legacy),
    the deliberate emitter-bypass is preserved — the emitter gets NO
    decide span at fan-out (reconnect lazy_fill compensates, per
    test_projection_end_to_end_wiring.test_emitter_reconnect_relies_on_lazy_fill).

    This pins the Track A change to the merged-MP path ONLY.
    """
    handler = _make_handler_three_pcs(tmp_path)
    _attach_queues(handler._room)

    from sidequest.server import session_handler as handler_module
    from sidequest.server import views as views_module

    class _FakeMsg:
        def __init__(self, payload):
            self.payload = payload

    monkeypatch.setitem(handler_module._KIND_TO_MESSAGE_CLS, "NARRATION", _FakeMsg)
    monkeypatch.setattr(views_module, "status_effects_by_player", lambda _h: {})

    exporter = _setup_tracing()
    exporter.clear()

    handler._emit_event("NARRATION", dict(_PAYLOAD))  # no author_player_id

    assert _decide_player_ids(exporter) == {"p_donut", "p_katia"}, (
        "solo/legacy: the emitter (p_carl) must remain bypassed at "
        "fan-out — only peers are projected live; lazy_fill covers the "
        "emitter on reconnect"
    )


# ---------------------------------------------------------------------------
# Wiring test (CLAUDE.md mandate): the PRODUCTION merged-MP narration emit
# must actually thread author_player_id == the driving (last-submitter)
# player. Drives a real 2-player barrier dispatch through
# _handle_player_action (proven pattern from test_mp_cinematic_dispatch)
# and spies _emit_event on the driving handler. Without this, the param
# could exist + be unit-tested yet never be passed by production code.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_merged_turn_threads_author_player_id(
    session_handler_factory,
) -> None:
    """p1 then p2 submit in a 2-seat room → barrier fires on p2's handler
    (p2 is the driver / last submitter). The production NARRATION emit
    inside ``_execute_narration_turn`` must call ``_emit_event`` with
    ``author_player_id == "p2"`` — proving the ADR-105 Track A call-site
    wiring is live, not just unit-tested in isolation."""
    from unittest.mock import patch

    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.protocol.messages import PlayerActionMessage, PlayerActionPayload
    from sidequest.protocol.types import NonBlankString

    handler1, sd1, room = session_handler_factory(
        slug="test-mp-adr105-wiring",
        mode=GameMode.MULTIPLAYER,
        seat_players=[("p1", "Gladstone"), ("p2", "Zanzibar")],
        active_player=("p1", "Gladstone"),
    )
    handler2, sd2, _ = session_handler_factory(
        slug="test-mp-adr105-wiring",
        mode=GameMode.MULTIPLAYER,
        seat_players=[("p1", "Gladstone"), ("p2", "Zanzibar")],
        active_player=("p2", "Zanzibar"),
        existing_room=room,
    )

    fake_result = NarrationTurnResult(
        narration="Gladstone steps forward while Zanzibar watches the dark.",
        is_degraded=False,
        agent_duration_ms=5,
    )
    sd1.orchestrator.run_narration_turn = AsyncMock(return_value=fake_result)
    sd2.orchestrator.run_narration_turn = AsyncMock(return_value=fake_result)

    captured: list[tuple[str, str | None]] = []

    def _spy_emit(self, kind, payload_model, *, author_player_id=None):
        captured.append((kind, author_player_id))

        class _M:
            def __init__(self, p):
                self.payload = p

        return _M(payload_model)

    # Spy _emit_event on the class so whichever handler drives the turn is
    # captured. Stub the heavy post-narration side-effects so the turn
    # reaches (and stops cleanly after) the NARRATION emit without pulling
    # the daemon / scrapbook / render stack into the test.
    with (
        patch.object(WebSocketSessionHandler, "_emit_event", _spy_emit),
        patch.object(WebSocketSessionHandler, "_emit_scrapbook_entry", lambda *a, **k: None),
    ):
        await handler1._handle_player_action(
            PlayerActionMessage(
                payload=PlayerActionPayload(
                    action=NonBlankString.model_validate("I step forward.")
                ),
                player_id="p1",
            )
        )
        await handler2._handle_player_action(
            PlayerActionMessage(
                payload=PlayerActionPayload(
                    action=NonBlankString.model_validate("I watch the dark.")
                ),
                player_id="p2",
            )
        )

    narration_authors = [author for kind, author in captured if kind == "NARRATION"]
    assert narration_authors, (
        "production merged turn never emitted NARRATION through _emit_event"
    )
    assert all(a == "p2" for a in narration_authors), (
        "merged-MP NARRATION must thread author_player_id == the driving "
        f"(last-submitter) player 'p2'; got {narration_authors!r}. The "
        "ADR-105 Track A call-site wiring is not live."
    )
