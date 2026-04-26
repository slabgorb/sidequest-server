"""Pingpong 2026-04-26 S2-BUG: Confrontations are PRIVATE to the acting player.

Repro from the Beatles 4-player session: Paul opened a Diplomatic Negotiation
vs Veriti Onua. Paul saw the confrontation card, NPC, and action buttons.
John, George, and Ringo's tabs froze on the prior shared beat — no NPC card,
no narration, no buttons.

Root cause: ``_execute_narration_turn`` built the ``ConfrontationMessage``
directly via ``ConfrontationMessage(...)`` and only appended it to the
acting player's ``outbound`` list — the same list returned to the caller
and pushed to the actor's socket. Peer sockets were never notified.

Fix: route the message through ``self._emit_event("CONFRONTATION", payload)``
so the canonical EventLog + ProjectionFilter fan-out path delivers a
per-player frame to every connected peer (mirrors NARRATION at L3365 of
session_handler.py).

This is a wiring test — it drives the actual ``_execute_narration_turn``
handler against a real :class:`SessionRoom` with four connected sockets
and asserts every non-acting peer's outbound queue receives a
CONFRONTATION frame.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.event_log import EventLog
from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.projection.cache import ProjectionCache
from sidequest.game.projection.composed import ComposedFilter
from sidequest.protocol.messages import ConfrontationMessage
from sidequest.server.session_room import RoomRegistry

_SLUG = "s2-confrontation-broadcast-test"


def _seed_game_row(tmp_path: Path) -> SqliteStore:
    db = db_path_for_slug(tmp_path, _SLUG)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=_SLUG,
        mode=GameMode.MULTIPLAYER,
        genre_slug="caverns_and_claudes",
        world_slug="",
    )
    return store


@pytest.mark.asyncio
async def test_confrontation_broadcasts_to_all_four_peer_sockets(
    session_handler_factory, tmp_path: Path,
) -> None:
    """4-player MP regression: every connected socket receives the
    CONFRONTATION frame on encounter start, not just the actor's socket.

    Drives the actual ``_execute_narration_turn`` handler — no mocked
    broadcast, no projection-filter shortcut. Wires up a real
    :class:`SessionRoom` with Paul, John, George, and Ringo connected on
    distinct sockets, fires the encounter via the orchestrator mock, and
    inspects each socket's ``asyncio.Queue`` for a delivered frame.

    Pre-fix: only Paul's outbound list (the function's return value)
    received the frame; John/George/Ringo's queues stayed empty.
    Post-fix: ``_emit_event("CONFRONTATION", ...)`` fans out to all three
    peer queues via the projection-filter pipeline.
    """
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.player_id = "paul"
    sd.player_name = "Paul"
    sd.mode = GameMode.MULTIPLAYER
    sd.game_slug = _SLUG

    # Real EventLog + ProjectionFilter so _emit_event takes the production
    # branch (not the legacy fallback). ComposedFilter.with_no_genre_rules
    # is a pass-through filter — every recipient receives the same payload,
    # which is the correct shared-world behavior for confrontation frames.
    store = _seed_game_row(tmp_path)
    handler._event_log = EventLog(store)
    handler._projection_filter = ComposedFilter.with_no_genre_rules()
    handler._projection_cache = ProjectionCache(store)

    # 4-socket multiplayer room. Paul is the actor; John/George/Ringo are
    # peers. Each socket gets its own asyncio.Queue — the production
    # fan-out path looks up queue_for_socket(socket_for_player(pid)) per
    # recipient, so unique queues per socket are required.
    registry = RoomRegistry()
    room = registry.get_or_create(slug=_SLUG, mode=GameMode.MULTIPLAYER)
    socket_ids = {
        "paul": "sock-paul",
        "john": "sock-john",
        "george": "sock-george",
        "ringo": "sock-ringo",
    }
    queues: dict[str, asyncio.Queue[object]] = {
        pid: asyncio.Queue() for pid in socket_ids
    }
    for pid, sid in socket_ids.items():
        room.connect(pid, socket_id=sid)
        room.attach_outbound(sid, queues[pid])
    handler._room = room
    handler._socket_id = socket_ids["paul"]

    # Orchestrator mock: opens a confrontation on this turn (encounter is
    # currently None on the snapshot; result.confrontation="combat" makes
    # the dispatch branch take the now_live path).
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(
            narration="Paul squares off against Veriti Onua.",
            confrontation="combat",
        ),
    )

    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "I open negotiations with Veriti Onua.", _build_turn_context(sd),
    )

    # Actor branch — Paul's outbound list still contains the
    # ConfrontationMessage (via the _emit_event return value).
    actor_conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(actor_conf) == 1, (
        f"Actor (Paul) should still receive the CONFRONTATION frame in "
        f"the returned outbound list; got message types "
        f"{[type(m).__name__ for m in msgs]}"
    )
    assert actor_conf[0].payload.active is True
    assert actor_conf[0].payload.type == "combat"

    # Peer branch — every non-acting socket queue must have received
    # exactly one CONFRONTATION frame. Pre-fix, these queues were empty
    # (the bug repro: peer tabs froze with no NPC card / no buttons).
    for peer_pid in ("john", "george", "ringo"):
        peer_frames: list[object] = []
        while not queues[peer_pid].empty():
            peer_frames.append(queues[peer_pid].get_nowait())
        peer_conf = [
            f for f in peer_frames if isinstance(f, ConfrontationMessage)
        ]
        assert len(peer_conf) == 1, (
            f"Peer {peer_pid!r} expected exactly one CONFRONTATION frame "
            f"on their queue; got {len(peer_conf)} (frames on queue: "
            f"{[type(f).__name__ for f in peer_frames]}). "
            "Pingpong S2-BUG: confrontations were PRIVATE to the actor "
            "before the _emit_event broadcast fix."
        )
        assert peer_conf[0].payload.active is True
        assert peer_conf[0].payload.type == "combat"

    # Paul's own queue should NOT have received a fan-out copy — the
    # emitter receives the canonical (raw) frame via the function return
    # value, not via their socket queue. The projection loop excludes the
    # emitter from the fan-out recipients (see _emit_event line ~871).
    paul_frames: list[object] = []
    while not queues["paul"].empty():
        paul_frames.append(queues["paul"].get_nowait())
    paul_conf_via_queue = [
        f for f in paul_frames if isinstance(f, ConfrontationMessage)
    ]
    assert paul_conf_via_queue == [], (
        "Emitter (Paul) should not receive a fan-out copy via their "
        "socket queue; they receive the canonical frame as the "
        "_emit_event return value (already verified above)."
    )
