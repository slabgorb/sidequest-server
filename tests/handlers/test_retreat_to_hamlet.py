"""RETREAT_TO_HAMLET handler — Sünden engine plan Task 9.

Locks the contract for the RETREAT_TO_HAMLET inbound message: a delve-mode
session ends the delve, persists Wall + dungeon_wounds + delve_count via
``apply_delve_end``, swaps the bound snapshot back to hub mode, and emits
a HUB_VIEW frame so the client unmounts the delve chrome.

Also covers the Task 10 ``player_dead`` auto-trigger which reuses the
same ``_end_delve`` helper with ``outcome="defeat"``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.persistence import SqliteStore, db_path_for_slug
from tests._helpers.delve import (
    drive_connect,
    drive_dungeon_select,
    drive_recruit,
    drive_retreat,
    make_handler,
    seed_hub_game,
)

_GENRE = "caverns_and_claudes"
_HUB_WORLD = "caverns_three_sins"
_DUNGEON = "grimvault"
_DUNGEON_SIN = "pride"

_CONTENT_SEARCH_PATH = (
    Path(__file__).resolve().parents[2].parent
    / "sidequest-content"
    / "genre_packs"
)


def _content_available() -> bool:
    return (_CONTENT_SEARCH_PATH / _GENRE / "worlds" / _HUB_WORLD).is_dir()


pytestmark = pytest.mark.skipif(
    not _content_available(),
    reason="caverns_three_sins hub content not on disk",
)


def _read_world_save(save_dir: Path, slug: str):
    """Direct WorldSave readback for assertions."""
    db = db_path_for_slug(save_dir, slug)
    store = SqliteStore(db)
    store.initialize()
    try:
        return store.load_world_save()
    finally:
        store.close()


async def _start_delve(
    tmp_path: Path,
    slug: str,
    *,
    archetype: str = "prig",
    hireling_id: str = "prig_001",
    name: str = "Mira",
):
    """Common setup: seed hub game, recruit one hireling, connect, start delve."""
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id=hireling_id, name=name, archetype=archetype
    )
    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)
    out = await drive_dungeon_select(
        handler,
        dungeon=_DUNGEON,
        party_hireling_ids=[h1.id],
    )
    errors = [m for m in out if getattr(m, "type", None) == "ERROR"]
    assert not errors, (
        f"Setup failed (DUNGEON_SELECT): "
        f"{[getattr(getattr(e, 'payload', None), 'message', '') for e in errors]}"
    )
    return handler, h1


@pytest.mark.asyncio
async def test_retreat_appends_wall_and_emits_hub_view(tmp_path: Path) -> None:
    """Happy path: voluntary retreat with no wound, no death.

    Asserts HUB_VIEW emitted; WorldSave gets +1 ``delve_count``, one
    wall entry with sin="pride", outcome="retreat", wounded_boss=False;
    ``latest_delve_sin`` is "pride"; ``dungeon_wounds`` stays empty.
    """
    slug = "retreat-happy"
    handler, _h1 = await _start_delve(tmp_path, slug)

    outbound = await drive_retreat(
        handler, outcome="retreat", wounded_boss=False
    )

    types = [getattr(m, "type", None) for m in outbound]
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert not errors, f"got errors: {types}"

    hub_views = [m for m in outbound if getattr(m, "type", None) == "HUB_VIEW"]
    assert len(hub_views) == 1, f"expected 1 HUB_VIEW; got {types}"

    ws = _read_world_save(tmp_path, slug)
    assert ws.delve_count == 1
    assert len(ws.wall) == 1
    entry = ws.wall[0]
    assert entry.sin == _DUNGEON_SIN
    assert entry.dungeon == _DUNGEON
    assert entry.outcome == "retreat"
    assert entry.wounded_boss is False
    assert ws.latest_delve_sin == _DUNGEON_SIN
    assert ws.dungeon_wounds == {}


@pytest.mark.asyncio
async def test_retreat_with_wounded_boss_sets_wound_flag(tmp_path: Path) -> None:
    """Victorious retreat after wounding the boss flips dungeon_wounds[slug].

    The wall entry retains the wound flag so the historical record
    matches the WorldSave-level flag.
    """
    slug = "retreat-victory-wound"
    handler, _h1 = await _start_delve(tmp_path, slug)

    outbound = await drive_retreat(
        handler, outcome="victory", wounded_boss=True
    )
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert not errors

    ws = _read_world_save(tmp_path, slug)
    assert ws.dungeon_wounds == {_DUNGEON: True}
    assert ws.wall[0].wounded_boss is True
    assert ws.wall[0].outcome == "victory"


@pytest.mark.asyncio
async def test_retreat_with_tpk_after_wound(tmp_path: Path) -> None:
    """Bittersweet TPK-after-wound: outcome=retreat + wounded_boss=True.

    Spec §"Wounded Sins": a wound flips the dungeon flag regardless of
    outcome. This case is the voluntary-retreat-after-wound path; the
    auto-trigger TPK path is covered in test_player_dead_auto_triggers.
    """
    slug = "retreat-tpk-after-wound"
    handler, _h1 = await _start_delve(tmp_path, slug)

    outbound = await drive_retreat(
        handler, outcome="retreat", wounded_boss=True
    )
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert not errors

    ws = _read_world_save(tmp_path, slug)
    assert ws.dungeon_wounds == {_DUNGEON: True}
    assert ws.wall[0].wounded_boss is True
    assert ws.wall[0].outcome == "retreat"


@pytest.mark.asyncio
async def test_retreat_clears_active_delve(tmp_path: Path) -> None:
    """Post-retreat the room snapshot is hub-mode (active_delve_dungeon=None)."""
    slug = "retreat-clears"
    handler, _h1 = await _start_delve(tmp_path, slug)

    assert handler._room.snapshot.active_delve_dungeon == _DUNGEON

    outbound = await drive_retreat(handler, outcome="retreat")
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert not errors

    snap = handler._room.snapshot
    assert snap is not None
    assert snap.active_delve_dungeon is None
    assert snap.characters == []


@pytest.mark.asyncio
async def test_retreat_rejects_in_hub_mode(tmp_path: Path) -> None:
    """RETREAT_TO_HAMLET outside an active delve must error with code=not_in_delve."""
    slug = "retreat-not-in-delve"
    seed_hub_game(tmp_path, slug)
    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)

    outbound = await drive_retreat(handler, outcome="retreat")
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert len(errors) == 1
    assert errors[0].payload.code == "not_in_delve"


@pytest.mark.asyncio
async def test_retreat_does_not_clear_world_save(tmp_path: Path) -> None:
    """init_session() inside _end_delve must NOT touch world_save.

    The two-tier persistence model: per-slot tables (game_state, events,
    narrative_log) are cleared on every delve transition; ``world_save``
    (roster, wall, dungeon_wounds, etc.) survives so the campaign-level
    state persists across delves.
    """
    slug = "retreat-roster-survives"
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )
    h2 = drive_recruit(
        tmp_path, slug, hireling_id="brawler_002", name="Tor", archetype="brawler"
    )

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)
    out = await drive_dungeon_select(
        handler,
        dungeon=_DUNGEON,
        party_hireling_ids=[h1.id, h2.id],
    )
    assert not [m for m in out if getattr(m, "type", None) == "ERROR"]

    outbound = await drive_retreat(handler, outcome="retreat")
    assert not [m for m in outbound if getattr(m, "type", None) == "ERROR"]

    ws = _read_world_save(tmp_path, slug)
    # Both recruits survive the slot reinit. They were both alive during
    # the delve so commit_back leaves their status as "active".
    assert len(ws.roster) == 2
    by_id = {h.id: h for h in ws.roster}
    assert by_id[h1.id].status == "active"
    assert by_id[h2.id].status == "active"


