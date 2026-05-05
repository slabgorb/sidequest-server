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
import random
import re
import secrets
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
from sidequest.genre.models.pack import Dungeon, GenrePack, World
from sidequest.genre.names import build_from_culture
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

    NOTE: ``dungeon`` is currently unused inside materialize_party's per-
    character path — reserved for downstream item-5/6 prompt-zone wiring
    (e.g. dungeon-specific opening descriptions). Do not remove.
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
            personality="Plain-spoken.",
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


# ---------------------------------------------------------------------------
# Item 4b — recruit roll helper
# ---------------------------------------------------------------------------


# Hireling.id pattern: ^[a-z][a-z0-9_]+$ (must start with a letter, then
# alphanumeric/underscore). Used to slugify the funnel name into the
# archetype prefix of a fresh hireling id.
_SLUG_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def _slugify_funnel_name(name: str) -> str:
    """Slug-shape a funnel display name for use in a Hireling.id prefix.

    'Cataloging Delver' → 'cataloging_delver'.
    Strips leading/trailing underscores and collapses runs.
    """
    s = _SLUG_NONALNUM_RE.sub("_", name.lower()).strip("_")
    if not s or not s[0].isalpha():
        # Shouldn't happen on well-authored content, but a name that begins
        # with a digit or punctuation would fail Hireling.id's pattern. Fail
        # loud — content authoring bug, not something to silently mask.
        raise ValueError(
            f"funnel name {name!r} slugifies to {s!r} which cannot prefix "
            "a Hireling.id (pattern ^[a-z][a-z0-9_]+$)"
        )
    return s


def _roll_hireling_from_funnels(
    *,
    pack: GenrePack,
    world: World,
    existing_ids: set[str],
    rng: random.Random | None = None,
) -> Hireling:
    """Roll a fresh hireling from the world's archetype_funnels.

    Picks a funnel weighted by ``len(funnel.absorbs)`` (a natural proxy for
    "how many axis-pair combos this funnel claims" — funnels do not carry
    an explicit ``weight`` field), generates a name via the genre pack's
    namegen path, and constructs a slug-shaped id like
    ``cataloging_delver_a3f1c2d4`` that satisfies Hireling.id's
    ``^[a-z][a-z0-9_]+$`` pattern.

    Raises ``ValueError`` when the world has no archetype_funnels (loader
    bug — Sünden hub worlds are required to ship one) or when no culture
    is available for namegen (genre/world authoring bug).

    Notes is set to ``"sin_origin: <slug>"`` when the funnel has a sin
    origin tag (Sünden hub-world feature) — this gives the narrator a
    surface-level cue without coupling the data model to the sin system.

    OTEL emission is the caller's responsibility (item 4b separates the
    pure roll from the watcher event; Task 12 owns the recruit/dismiss
    span).
    """
    if rng is None:
        rng = random.Random()
    if world.archetype_funnels is None or not world.archetype_funnels.funnels:
        raise ValueError(
            "world has no archetype_funnels; cannot roll a hireling. "
            "Sünden hub worlds must ship archetype_funnels.yaml."
        )
    funnels = world.archetype_funnels.funnels
    weights = [max(1, len(f.absorbs)) for f in funnels]
    chosen = rng.choices(funnels, weights=weights, k=1)[0]

    archetype_slug = _slugify_funnel_name(chosen.name)
    name = _generate_hireling_name(pack=pack, world=world, rng=rng)

    # Collision avoidance against ``existing_ids``. 4 hex bytes → 2^32
    # combinations; collisions are astronomically unlikely but defended.
    # Bounded loop instead of ``while True`` so a degenerate caller (e.g.
    # an existing_ids set that somehow contains every possible id) fails
    # loud instead of hanging.
    for _ in range(64):
        candidate_id = f"{archetype_slug}_{secrets.token_hex(4)}"
        if candidate_id not in existing_ids:
            break
    else:
        raise RuntimeError(
            f"could not allocate a unique hireling id after 64 attempts "
            f"(archetype_slug={archetype_slug!r}, "
            f"existing_id_count={len(existing_ids)})"
        )

    notes = f"sin_origin: {chosen.sin_origin}" if chosen.sin_origin else ""

    # Pydantic validation enforces the id pattern; a failure here is a
    # code bug (slugifier returned something invalid) — fail loud, do
    # not normalize.
    return Hireling(
        id=candidate_id,
        name=name,
        archetype=archetype_slug,
        notes=notes,
    )


def _generate_hireling_name(
    *,
    pack: GenrePack,
    world: World,
    rng: random.Random,
) -> str:
    """Generate a person name via the genre pack's namegen pipeline.

    Reuses ``build_from_culture`` (the existing namegen entry point used
    by ``sidequest.cli.namegen.namegen``). Picks the world's culture if
    one is configured; otherwise falls back to the genre pack's culture
    list. Raises ``ValueError`` when neither has a culture (authoring
    bug — the corpus pipeline cannot run without one).
    """
    cultures = list(world.cultures) if world.cultures else list(pack.cultures)
    if not cultures:
        raise ValueError(
            "no cultures available for namegen "
            f"(pack={pack.meta.name!r}, world={world.config.name!r}); "
            "cannot generate a hireling name"
        )
    if pack.source_dir is None:
        raise ValueError(
            f"pack {pack.meta.name!r} has no source_dir; "
            "cannot resolve corpus directory for namegen"
        )
    culture = rng.choice(cultures)
    corpus_dir = pack.source_dir / "corpus"
    generator = build_from_culture(culture, corpus_dir, rng)
    # Bounded retries — namegen can produce empty / "of "-prefixed
    # strings on small corpora; bail loudly rather than infinite-loop.
    for _ in range(20):
        candidate = generator.generate_person()
        lower = candidate.lower()
        if candidate and not lower.startswith("of ") and not lower.startswith("the "):
            return candidate
    raise RuntimeError(
        f"namegen produced no acceptable name after 20 attempts "
        f"(culture={culture.name!r})"
    )
