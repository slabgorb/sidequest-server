"""DungeonSelectHandler — start a delve from a hub-mode session.

Sünden engine plan Task 8. Inbound transition: hub → delve.

Pre-conditions (loud rejection on violation, never silent):
  * The room must be in hub mode (no ``active_delve_dungeon`` set). A
    second DUNGEON_SELECT while already delving is a client bug; reply
    with ``code="delve_already_active"`` rather than silently restarting.
  * The bound world must actually be a hub world (has dungeons). A
    DUNGEON_SELECT against a leaf world is a protocol misuse;
    ``code="not_a_hub_world"``.
  * The requested dungeon slug must exist on the world;
    ``code="unknown_dungeon"``.
  * The requested party must satisfy the validation rules in
    ``materialize_party`` (size 1..6, no duplicates, all in roster, all
    active). Any failure surfaces as ``code="invalid_party"`` with the
    underlying ValueError message.

On success:
  * ``init_session`` clears every per-slot table so the new delve starts
    on a fresh game_state / events / narrative_log slot.
    ``world_save`` survives (roster, wall, etc.) — see
    ``_PER_SLOT_TABLES`` for the exact list.
  * The new ``GameSnapshot`` is bound to the room with
    ``active_delve_dungeon`` set, party characters materialized, and
    location seeded from the dungeon's first opening.
  * The snapshot is persisted via ``room.save()`` so a reconnect mid-
    delve lands on the standard mid-delve resume path (verified in
    ``tests/handlers/test_connect.py::test_connect_resumes_mid_delve``).

Opening narration is intentionally NOT emitted by this handler. The
plan §7 sketch suggested an inline emission, but the existing opening-
narration code path is welded into ``connect.py``'s post-chargen flow
and extracting it cleanly is beyond the scope of Task 8 (the
acceptance criteria only assert snapshot mutation). The next inbound
PLAYER_ACTION will run the standard narrator turn against the bound
delve snapshot.

The ``session.delve_started`` OTEL watcher event is intentionally NOT
emitted here — that lands in Task 12 alongside the rest of the engine
plan's observability surface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.game.delve_lifecycle import is_hub_world, materialize_party
from sidequest.game.persistence import db_path_for_slug, get_game
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import GenreLoader
from sidequest.server.session_helpers import _error_msg

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


def _opening_location(dungeon) -> str:
    """Pick the opening location for a dungeon.

    Prefer the first opening's location_label (the human-readable
    setting authored in ``openings.yaml``), falling back to the
    dungeon's display name. No silent default — if the dungeon ships
    with neither, raise so the content authoring error is loud.
    """
    if dungeon.openings:
        first = dungeon.openings[0]
        label = first.setting.location_label
        if label:
            return label
    name = dungeon.config.name
    if name:
        return name
    raise ValueError(
        f"dungeon {dungeon.config.parent_world!r} has no opening location_label "
        "and no display name; cannot seed delve location"
    )


class DungeonSelectHandler:
    """Handle a DUNGEON_SELECT inbound message."""

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]:
        room = session._room
        if room is None:
            logger.info(
                "session.dungeon_select_unbound state=%s",
                session._state.name,
            )
            return [
                _error_msg(
                    "Cannot process DUNGEON_SELECT: no active session. "
                    "Connect to a slug first.",
                    code="session_unbound",
                )
            ]

        # Already-delving guard. The room's snapshot is bound only when
        # the prior delve started here; in fresh hub mode (post-connect,
        # pre-DUNGEON_SELECT) ``room.snapshot`` is None — that's the
        # happy path. A non-None snapshot with ``active_delve_dungeon``
        # set means a client sent DUNGEON_SELECT twice without a
        # RETREAT_TO_HAMLET in between.
        if room.snapshot is not None and room.snapshot.active_delve_dungeon is not None:
            return [
                _error_msg(
                    f"already delving in "
                    f"{room.snapshot.active_delve_dungeon!r}; "
                    "send RETREAT_TO_HAMLET first",
                    code="delve_already_active",
                )
            ]

        # Resolve the slug + genre/world via the room + games table.
        # The hub-mode connect doesn't populate ``session._session_data``,
        # so we go through the games table directly.
        slug = room.slug
        db = db_path_for_slug(session._save_dir, slug)
        if not db.exists():
            return [
                _error_msg(
                    f"unknown game slug: {slug}",
                    code="unknown_slug",
                )
            ]
        from sidequest.game.persistence import SqliteStore

        store = SqliteStore(db)
        store.initialize()
        row = get_game(store, slug)
        if row is None:
            store.close()
            return [
                _error_msg(
                    f"unknown game slug: {slug}",
                    code="unknown_slug",
                )
            ]

        try:
            loader = GenreLoader(search_paths=session._search_paths)
            pack = loader.load(row.genre_slug)
            world_dir = loader.find(row.genre_slug) / "worlds" / row.world_slug
        except Exception as exc:
            store.close()
            logger.error(
                "session.dungeon_select.genre_load_failed genre=%s slug=%s error=%s",
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
        if world is None or not is_hub_world(world):
            store.close()
            return [
                _error_msg(
                    f"world {row.world_slug!r} is not a hub world; "
                    "DUNGEON_SELECT is only valid in hub mode",
                    code="not_a_hub_world",
                )
            ]

        payload = msg.payload  # type: ignore[attr-defined]
        dungeon = world.dungeons.get(payload.dungeon)
        if dungeon is None:
            store.close()
            return [
                _error_msg(
                    f"unknown dungeon: {payload.dungeon!r}",
                    code="unknown_dungeon",
                )
            ]

        world_save = store.load_world_save()
        try:
            party = materialize_party(
                roster=world_save.roster,
                party_ids=payload.party_hireling_ids,
                world_slug=row.world_slug,
                dungeon=dungeon,
            )
        except ValueError as exc:
            store.close()
            return [_error_msg(str(exc), code="invalid_party")]

        try:
            opening_location = _opening_location(dungeon)
        except ValueError as exc:
            store.close()
            return [_error_msg(str(exc), code="dungeon_missing_opening")]

        # Per the plan: clear per-slot tables so the new delve gets a
        # fresh game_state / events / narrative_log slot. world_save
        # (roster, wall, dungeon_wounds, etc.) is preserved by
        # init_session — see _PER_SLOT_TABLES in persistence.py.
        store.init_session(row.genre_slug, row.world_slug)

        new_snapshot = GameSnapshot(
            genre_slug=row.genre_slug,
            world_slug=row.world_slug,
            location=opening_location,
            active_delve_dungeon=payload.dungeon,
            characters=party,
        )
        # bind_world is idempotent — first bind wins. Hub-mode connect
        # doesn't bind, so this is the first bind on the room.
        room.bind_world(snapshot=new_snapshot, store=store, world_dir=world_dir)
        room.save()

        logger.info(
            "session.delve_started slug=%s dungeon=%s party_size=%d",
            slug,
            payload.dungeon,
            len(party),
        )

        # No outbound frame — the next inbound (typically a SESSION_EVENT
        # reconnect from the client after the UI swaps from hub to delve
        # view) will pick up the bound snapshot via the standard mid-
        # delve resume path. Task 12 adds the OTEL watcher event;
        # opening narration emission is the next task's territory.
        return []


HANDLER = DungeonSelectHandler()
