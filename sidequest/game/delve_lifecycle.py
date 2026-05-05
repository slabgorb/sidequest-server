"""Delve lifecycle — start, end, materialize, commit-back.

Sünden engine plan item 4a. Pure functions only — no I/O, no DB access,
no global state. Callers (REST handlers, websocket dispatch) own the
persistence step.

The verbs are the only public surface:

- ``is_hub_world(world)`` — single source of truth for the hub-vs-leaf
  check (encapsulates ``bool(world.dungeons)``).
- ``build_available_dungeons(world, world_save)`` — compose the
  enriched ``AvailableDungeon`` list shipped in HUB_VIEW. Server-side
  resolution of ``{slug, sin, wounded}`` so the client never carries a
  hard-coded SIN_BY_DUNGEON map.
- ``materialize_party(roster, party_ids, *, world_slug, dungeon)`` —
  copy roster identity into Character shapes (with ``hireling_id``
  linkage); raise on missing/dead/duplicate ids. Does NOT carry stress
  (item 3 owns stress).
- ``commit_back(snapshot, world_save)`` — pure: takes the delve-end
  snapshot, returns the WorldSave with hireling status updated by id.
  Match is by ``Character.hireling_id`` → ``Hireling.id``, never by
  name. Does NOT touch stress (item 3). Caller persists.
- ``apply_delve_end(world_save, *, ...)`` — full delve-end business
  rules (commit-back + Wall append + drift flag + wound flag (when
  ``wounded_boss=True``) + ``delve_count++``). Returns updated
  WorldSave; caller persists.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from sidequest.game.character import Character
from sidequest.game.creature_core import (
    CreatureCore,
    Inventory,
    placeholder_edge_pool,
)
from sidequest.game.session import GameSnapshot
from sidequest.game.world_save import Hireling, WallEntry, WorldSave
from sidequest.genre.models.pack import Dungeon, World
from sidequest.protocol.messages import AvailableDungeon

logger = logging.getLogger(__name__)


def is_hub_world(world: World) -> bool:
    """True iff the world has any dungeons (hub-shaped per pack.py docstring).

    Invariant from ``World``: ``cartography is None ⇔ dungeons is non-empty``.
    Reading ``dungeons`` is the simpler check and the one the wire
    protocol cares about (a hub world is one the player can pick a
    dungeon from).
    """
    return bool(world.dungeons)


def build_available_dungeons(
    world: World,
    world_save: WorldSave,
) -> list[AvailableDungeon]:
    """Compose the enriched dungeon list shipped in HUB_VIEW.

    Server-side resolution of ``{slug, sin, wounded}`` so the client
    never needs a hard-coded SIN_BY_DUNGEON map. Sin comes from the
    dungeon's ``Dungeon.config.sin`` (loader item 1), wounded comes
    from ``world_save.dungeon_wounds``. Sorted by slug for deterministic
    UI order.

    Raises ``ValueError`` if a dungeon's ``sin`` is unset — the loader
    is supposed to enforce sin presence on hub-world dungeons; reaching
    this code with ``sin=None`` is a content authoring bug, not
    something to silently default.
    """
    items: list[AvailableDungeon] = []
    for slug in sorted(world.dungeons):
        sin = world.dungeons[slug].config.sin
        if sin is None:
            # No silent fallback — loader is supposed to require ``sin`` on
            # hub-world dungeons. Reaching here means the content drifted.
            raise ValueError(
                f"dungeon {slug!r} has no sin configured; "
                "cannot build AvailableDungeon entry"
            )
        items.append(
            AvailableDungeon(
                slug=slug,
                sin=sin,
                wounded=world_save.dungeon_wounds.get(slug, False),
            )
        )
    return items


def materialize_party(
    roster: list[Hireling],
    party_ids: list[str],
    *,
    world_slug: str,
    dungeon: Dungeon,
) -> list[Character]:
    """Materialize a delve party from the roster.

    Raises ``ValueError`` on invalid input — the websocket dispatch
    layer turns that into a typed wire error.

    Validation order matters (test-checked):
      1. party size 1..6
      2. no duplicate ids in ``party_ids``
      3. all ids exist in ``roster``
      4. all referenced hirelings have ``status == "active"``

    Stress is NOT propagated to Character (item 3 territory).
    """
    if not (1 <= len(party_ids) <= 6):
        raise ValueError(f"party size must be 1..6, got {len(party_ids)}")
    if len(set(party_ids)) != len(party_ids):
        raise ValueError(f"party_hireling_ids contains duplicates: {party_ids}")
    by_id = {h.id: h for h in roster}
    missing = [pid for pid in party_ids if pid not in by_id]
    if missing:
        raise ValueError(f"hirelings not in roster: {missing}")
    inactive = [pid for pid in party_ids if by_id[pid].status != "active"]
    if inactive:
        raise ValueError(f"hirelings not active: {inactive}")

    return [
        _character_from_hireling(by_id[pid], world_slug=world_slug)
        for pid in party_ids
    ]


def _character_from_hireling(hireling: Hireling, *, world_slug: str) -> Character:
    """Build a Character from a roster Hireling.

    The unified character model (ADR-007) carries narrative + mechanical
    identity. Hirelings are slim — they only carry ``id``, ``name``,
    ``archetype``, ``stress``, ``status``. This function fills the
    Character with safe defaults for everything else and pins the
    delve-lifecycle fields (``hireling_id``, ``resolved_archetype``)
    that commit-back relies on.

    NOTE on the archetype field: the plan §5 spec asserts
    ``character.core.archetype``, but ``CreatureCore`` is
    ``extra="forbid"`` and has no ``archetype`` field. Adding one would
    be a load-bearing schema change driven by an aspirational test;
    ``Character.resolved_archetype`` (already present, P2-deferred) is
    the correct home for a resolved archetype slug. The materializer
    pins that field; downstream prompt-building reads it.

    Stress is NOT carried (item 3 territory). The narrator has access
    to the Hireling.stress via WorldSave on the chargen-adjacent prompt
    zone; per-Character stress mechanics land in item 3's plan.
    """
    return Character(
        core=CreatureCore(
            name=hireling.name,
            description=f"A roster member of {world_slug}.",
            personality="placeholder",
            inventory=Inventory(),
            statuses=[],
            edge=placeholder_edge_pool(),
        ),
        backstory=f"Recruited from the hamlet of {world_slug}.",
        char_class="Adventurer",
        race="Human",
        hireling_id=hireling.id,
        resolved_archetype=hireling.archetype,
        is_dead=False,
    )


def commit_back(
    snapshot: GameSnapshot,
    world_save: WorldSave,
) -> WorldSave:
    """Pure: copy alive/dead status from delve characters back to roster.

    Match is by ``Character.hireling_id`` → ``Hireling.id``, NEVER by
    name. Namegen has finite culture-corpus entropy and two hirelings
    can share a display name; matching by name would silently
    misattribute deaths. Characters with ``hireling_id is None`` (e.g.
    legacy chargen-spawned PCs from non-hub flows) are skipped — they
    have no roster row to write back to.

    Stress is NOT touched. The Hireling-side ``stress`` field stays
    exactly as item 2 left it for the duration of this plan; item 3's
    plan owns stress accrual at both ends.
    """
    by_id = {h.id: h for h in world_save.roster}
    new_roster: list[Hireling] = list(world_save.roster)
    for ch in snapshot.characters:
        hid = ch.hireling_id
        if hid is None:
            continue  # legacy PC; not roster-tracked
        h = by_id.get(hid)
        if h is None:
            # Hireling was dismissed mid-delve (impossible by current REST
            # gating, but defended here). Loud log, not silent skip.
            logger.warning(
                "commit_back: character %r references hireling_id=%r "
                "absent from roster; status update skipped",
                ch.core.name,
                hid,
            )
            continue
        idx = new_roster.index(h)
        new_status = "dead" if ch.is_dead else h.status
        new_roster[idx] = h.model_copy(update={"status": new_status})
    return world_save.model_copy(update={"roster": new_roster})


def apply_delve_end(
    world_save: WorldSave,
    *,
    dungeon_slug: str,
    dungeon_sin: str,  # from Dungeon.config.sin at the call site
    outcome: Literal["retreat", "victory", "defeat"],
    wounded_boss: bool,
    party_hireling_ids: list[str],
    snapshot: GameSnapshot,
    timestamp: datetime,
) -> WorldSave:
    """Apply all delve-end mutations to WorldSave and return the new value.

    ``outcome`` is party fate; ``wounded_boss`` is the orthogonal flag
    (spec §"Wounded Sins"). A wound flips ``dungeon_wounds[slug]=True``
    regardless of outcome — TPK-after-wound is a real recordable event.
    Once a dungeon is wounded, it stays wounded (spec line 89: "A
    dungeon can only be wounded once in this design").

    Caller persists the returned WorldSave.
    """
    ws = commit_back(snapshot, world_save)
    new_count = ws.delve_count + 1
    new_wall = ws.wall + [
        WallEntry(
            delve_number=new_count,
            sin=dungeon_sin,
            dungeon=dungeon_slug,
            party_hireling_ids=party_hireling_ids,
            outcome=outcome,
            wounded_boss=wounded_boss,
            timestamp=timestamp,
        )
    ]
    new_wounds = dict(ws.dungeon_wounds)
    if wounded_boss:
        new_wounds[dungeon_slug] = True
    return ws.model_copy(
        update={
            "wall": new_wall,
            "dungeon_wounds": new_wounds,
            "latest_delve_sin": dungeon_sin,
            "delve_count": new_count,
        }
    )
