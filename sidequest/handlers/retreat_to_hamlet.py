"""RetreatToHamletHandler — end a delve and return to the hub.

Sünden engine plan Task 9. Inbound transition: delve → hub.

Pre-conditions (loud rejection on violation, never silent):
  * The room must be in delve mode (``room.snapshot.active_delve_dungeon``
    set). RETREAT_TO_HAMLET in hub mode (or pre-bind) is a client bug;
    reply with ``code="not_in_delve"`` rather than silently no-op'ing.

On success:
  * ``apply_delve_end`` (pure) computes the new WorldSave: commit-back
    of hireling alive/dead status, +1 ``delve_count``, fresh
    ``WallEntry`` (sin from ``Dungeon.config.sin``, party = current
    snapshot characters' ``hireling_id`` list), ``latest_delve_sin``
    overwritten, ``dungeon_wounds[slug] = True`` iff ``wounded_boss``.
  * The new WorldSave is persisted via ``store.save_world_save``.
  * ``store.init_session(genre, world)`` clears every per-slot table
    (``_PER_SLOT_TABLES``); the ``world_save`` row survives — that's
    the whole point of the two-tier persistence model in
    ``sidequest/game/world_save.py``.
  * A fresh hub-mode ``GameSnapshot`` (``active_delve_dungeon=None``,
    no characters, no location) is built and rebound to the room via
    ``room.rebind_world(...)``. ``bind_world`` is idempotent and
    short-circuits when a snapshot is already bound; the explicit
    ``rebind_world`` is the supported swap path.
  * A HUB_VIEW frame is emitted so the client unmounts the delve
    chrome and renders the hamlet hub with the updated wall + roster.

The ``_end_delve`` helper lives in this module rather than in
``delve_lifecycle.py`` because it is NOT pure — it does store I/O
(persist WorldSave, reinit slot tables) and mutates the room. The
pure delve-end math stays in ``delve_lifecycle.apply_delve_end``;
this module is the I/O envelope around it. Task 10's player_dead
auto-trigger imports ``_end_delve`` from this module — the cross-
import is intentional and documented in the engine plan.

The ``session.delve_ended`` OTEL watcher event is intentionally NOT
emitted here — that lands in Task 12 alongside the rest of the engine
plan's observability surface.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from sidequest.game.delve_lifecycle import apply_delve_end, build_available_dungeons
from sidequest.game.persistence import get_game
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import GenreLoader
from sidequest.protocol.messages import (
    HubViewMessage,
    HubViewPayload,
    RetreatToHamletMessage,
)
from sidequest.server.session_helpers import _error_msg
from sidequest.telemetry.spans.session import SPAN_SESSION_DELVE_ENDED
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

if TYPE_CHECKING:
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


async def _end_delve(
    *,
    session: WebSocketSessionHandler,
    slug: str,
    outcome: Literal["retreat", "victory", "defeat"],
    wounded_boss: bool,
) -> list[object]:
    """Shared delve-end path. Used by RETREAT_TO_HAMLET and player_dead.

    Accepts the broader ``Literal["retreat", "victory", "defeat"]``
    even though the wire-inbound ``RetreatToHamletPayload.outcome`` is
    only ``Literal["retreat", "victory"]``. The difference is
    intentional: ``"defeat"`` is server-only, fired by Task 10's
    ``player_dead`` auto-trigger. The wire type stays narrow so a
    client cannot claim defeat.
    """
    room = session._room
    if room is None or room.snapshot is None:
        # Programming-error path — callers must have validated the
        # delve-mode precondition before reaching here.
        raise RuntimeError(
            "_end_delve called without a bound room+snapshot — "
            "callers must validate room.snapshot is not None first"
        )
    snapshot = room.snapshot
    dungeon_slug = snapshot.active_delve_dungeon
    if dungeon_slug is None:
        raise RuntimeError(
            "_end_delve called outside an active delve — "
            "callers must validate active_delve_dungeon first"
        )

    # Use the room's canonical SqliteStore — opening a sibling handle
    # against the same DB file would violate ADR-037 (the room owns the
    # store) AND leak the prior handle when ``rebind_world`` swaps it.
    # The room is already bound by DUNGEON_SELECT/CONNECT, so the store
    # is guaranteed non-None on the success path of those handlers.
    store = room._store
    if store is None:
        # Programming-error path — a bound room+snapshot without a bound
        # store would mean DUNGEON_SELECT/CONNECT skipped the bind_world
        # store= argument. Fail loud rather than silently re-open.
        raise RuntimeError(
            "_end_delve called on a room with no bound store — "
            "bind_world must be called with store= before delve-end"
        )
    row = get_game(store, slug)
    if row is None:
        return [_error_msg(f"unknown game slug: {slug}", code="unknown_slug")]

    try:
        loader = GenreLoader(search_paths=session._search_paths)
        pack = loader.load(row.genre_slug)
        world_dir = loader.find(row.genre_slug) / "worlds" / row.world_slug
    except Exception as exc:
        logger.error(
            "session.end_delve.genre_load_failed genre=%s slug=%s error=%s",
            row.genre_slug,
            slug,
            exc,
        )
        return [
            _error_msg(
                f"Failed to load genre pack '{row.genre_slug}': {exc}",
                code="genre_load_failed",
            )
        ]

    world = pack.worlds.get(row.world_slug)
    if world is None:
        return [
            _error_msg(
                f"world {row.world_slug!r} not found in pack {row.genre_slug!r}",
                code="unknown_world",
            )
        ]
    dungeon = world.dungeons.get(dungeon_slug)
    if dungeon is None:
        # Dungeon disappeared between delve-start and delve-end —
        # only possible if content was hot-swapped mid-session. Fail
        # loud rather than silently lose the wall entry.
        return [
            _error_msg(
                f"dungeon {dungeon_slug!r} no longer exists in world "
                f"{row.world_slug!r}; cannot end delve cleanly",
                code="dungeon_missing_at_delve_end",
            )
        ]
    if dungeon.config.sin is None:
        return [
            _error_msg(
                f"dungeon {dungeon_slug!r} has no sin configured; "
                "cannot record wall entry",
                code="dungeon_missing_sin",
            )
        ]

    party_hireling_ids = [
        c.hireling_id for c in snapshot.characters if c.hireling_id is not None
    ]

    world_save = store.load_world_save()
    new_world_save = apply_delve_end(
        world_save,
        dungeon_slug=dungeon_slug,
        dungeon_sin=dungeon.config.sin,
        outcome=outcome,
        wounded_boss=wounded_boss,
        party_hireling_ids=party_hireling_ids,
        snapshot=snapshot,
        timestamp=datetime.now(tz=UTC),
    )
    store.save_world_save(new_world_save)
    # Clear per-slot tables — game_state, events, narrative_log, etc.
    # ``world_save`` survives (see ``_PER_SLOT_TABLES`` in
    # persistence.py). Without this the next DUNGEON_SELECT would
    # observe stale narrative_log rows from the just-ended delve.
    store.init_session(row.genre_slug, row.world_slug)

    fresh = GameSnapshot(
        genre_slug=row.genre_slug,
        world_slug=row.world_slug,
        active_delve_dungeon=None,
    )
    # ``bind_world`` short-circuits when a snapshot is already bound.
    # The room currently holds the delve-mode snapshot, so the
    # explicit rebind helper is the right path. ``store=`` is omitted
    # so ``rebind_world`` preserves the canonical store the room
    # already owns (avoids dropping a live SQLite handle on the floor).
    room.rebind_world(snapshot=fresh, world_dir=world_dir)
    room.save()

    # Reload after persistence so the HUB_VIEW reads the just-written
    # row (rather than a stale cached one). save_world_save also stamps
    # ``last_saved_at`` which the GM panel reads.
    final_world_save = store.load_world_save()

    _watcher_publish(
        SPAN_SESSION_DELVE_ENDED,
        {
            "slug": slug,
            "dungeon": dungeon_slug,
            "outcome": outcome,
            "party_size": len(snapshot.characters),
            "delve_count_after": new_world_save.delve_count,
        },
        component="session",
    )

    logger.info(
        "session.delve_ended slug=%s dungeon=%s outcome=%s wounded_boss=%s "
        "delve_count=%d",
        slug,
        dungeon_slug,
        outcome,
        wounded_boss,
        new_world_save.delve_count,
    )

    return [
        HubViewMessage(
            payload=HubViewPayload(
                slug=slug,
                genre_slug=row.genre_slug,
                world_slug=row.world_slug,
                available_dungeons=build_available_dungeons(world, final_world_save),
                world_save=final_world_save,
            ),
        ),
    ]


async def maybe_end_delve_on_player_dead(
    *,
    session: WebSocketSessionHandler,
    slug: str,
    prev_player_dead: bool,
    snapshot: GameSnapshot,
) -> list[object]:
    """Task 10 trigger — fire ``_end_delve(outcome="defeat")`` on positive edge.

    Called by the dispatch path immediately after
    ``_apply_narration_result_to_snapshot``. ``prev_player_dead`` is
    captured before the apply; ``snapshot`` is the post-apply
    snapshot. The combined check (positive edge AND active delve)
    prevents a double-fire when the same flag stays set across turns
    (e.g. resume into a save where the PC is already dead but the
    delve was previously ended).

    The auto-fire path uses ``outcome="defeat"`` and
    ``wounded_boss=False`` — the latter because by the time the PC is
    dead the narrator has already committed to "TPK without wounding";
    a "TPK after wounding" path would need an explicit flag from the
    narrator's structured output, which Task 10 does not yet wire.
    """
    if (
        not prev_player_dead
        and snapshot.player_dead
        and snapshot.active_delve_dungeon is not None
    ):
        return await _end_delve(
            session=session,
            slug=slug,
            outcome="defeat",
            wounded_boss=False,
        )
    return []


class RetreatToHamletHandler:
    """Handle a RETREAT_TO_HAMLET inbound message."""

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: RetreatToHamletMessage,
    ) -> list[object]:
        room = session._room
        if room is None or room.snapshot is None:
            return [
                _error_msg(
                    "not currently in a delve",
                    code="not_in_delve",
                )
            ]
        snapshot = room.snapshot
        if snapshot.active_delve_dungeon is None:
            return [
                _error_msg(
                    "not currently in a delve",
                    code="not_in_delve",
                )
            ]
        payload = msg.payload
        return await _end_delve(
            session=session,
            slug=room.slug,
            outcome=payload.outcome,
            wounded_boss=payload.wounded_boss,
        )


HANDLER = RetreatToHamletHandler()
