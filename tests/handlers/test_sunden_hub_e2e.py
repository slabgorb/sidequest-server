"""End-to-end happy path for the Sünden delve-lifecycle engine (Task 13).

This is the "Keith would actually do this" test: walk the full hub →
delve → retreat → delve loop in a single handler instance, mixing the
REST recruit endpoint with WebSocket DUNGEON_SELECT / RETREAT_TO_HAMLET
dispatch. Catches integration bugs the per-task unit tests can't see —
in particular that the second DUNGEON_SELECT (after the first retreat)
binds cleanly against the hub-mode snapshot left behind by ``_end_delve``.

Recruit is driven through the production REST endpoint via FastAPI
``TestClient`` rather than ``drive_recruit`` — the helper is a direct
WorldSave manipulator and would mask any wiring bug between the recruit
handler, ``save_world_save``, and the WS handler's reads. The TestClient
+ handler combo works because both share the on-disk SQLite store.

Lives in ``tests/handlers/`` (not ``tests/integration/``) because that
directory has a circular-import collection failure pre-dating this work
— same workaround Task 11 used.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.game.persistence import SqliteStore, db_path_for_slug
from sidequest.protocol.enums import MessageType
from sidequest.server.app import create_app
from tests._helpers.delve import (
    drive_connect,
    drive_dungeon_select,
    drive_retreat,
    make_handler,
    seed_hub_game,
)

_GENRE = "caverns_and_claudes"
_HUB_WORLD = "caverns_three_sins"

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
    db = db_path_for_slug(save_dir, slug)
    store = SqliteStore(db)
    store.initialize()
    try:
        return store.load_world_save()
    finally:
        store.close()


def _hub_views(outbound: list[object]):
    return [m for m in outbound if getattr(m, "type", None) == MessageType.HUB_VIEW]


def _errors(outbound: list[object]):
    return [m for m in outbound if getattr(m, "type", None) == "ERROR"]


@pytest.mark.asyncio
async def test_full_hub_delve_retreat_delve_loop(tmp_path: Path) -> None:
    """Walk the entire loop end-to-end: connect → recruit×2 → delve →
    retreat (victory, no wound) → recruit → second delve (different
    dungeon) → retreat (victory, wounded boss).

    Asserts each transition's wire output and persisted state at every
    step. The second DUNGEON_SELECT is the gnarly case: ``_end_delve``
    rebinds the room with a hub-mode snapshot, and the next select must
    succeed against that without tripping the ``delve_already_active``
    guard.
    """
    slug = "sunden-e2e-loop"
    seed_hub_game(tmp_path, slug)

    # Shared FastAPI app for REST recruits. Same ``save_dir`` as the
    # WS handler so they collaborate through the on-disk SQLite store.
    app = create_app(
        save_dir=tmp_path,
        genre_pack_search_paths=[_CONTENT_SEARCH_PATH],
    )
    rest = TestClient(app)

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])

    # ---- Step 1: hub-mode connect emits HUB_VIEW ----
    out = await drive_connect(handler, slug)
    assert not _errors(out), out
    hubs = _hub_views(out)
    assert len(hubs) == 1, f"expected one HUB_VIEW on hub connect; got {out}"
    payload = hubs[0].payload  # type: ignore[attr-defined]
    assert payload.slug == slug
    assert payload.genre_slug == _GENRE
    assert payload.world_slug == _HUB_WORLD
    # Content invariant: three dungeons sorted by slug, with their sins
    # and wounded=False on a fresh save.
    available = [(d.slug, d.sin, d.wounded) for d in payload.available_dungeons]
    assert available == [
        ("grimvault", "pride", False),
        ("horden", "greed", False),
        ("mawdeep", "gluttony", False),
    ]
    assert payload.world_save.roster == []
    assert payload.world_save.delve_count == 0

    # ---- Step 2: recruit two hirelings via REST ----
    r1 = rest.post(f"/api/games/{slug}/hub/recruit")
    assert r1.status_code == 200, r1.text
    h1_id = r1.json()["id"]
    r2 = rest.post(f"/api/games/{slug}/hub/recruit")
    assert r2.status_code == 200, r2.text
    h2_id = r2.json()["id"]
    assert h1_id != h2_id
    ws = _read_world_save(tmp_path, slug)
    assert len(ws.roster) == 2
    roster_ids = {h.id for h in ws.roster}
    assert {h1_id, h2_id} == roster_ids

    # ---- Step 3: DUNGEON_SELECT(grimvault, [both]) starts the delve ----
    out = await drive_dungeon_select(
        handler, dungeon="grimvault", party_hireling_ids=[h1_id, h2_id]
    )
    assert not _errors(out), out
    snap = handler._room.snapshot
    assert snap is not None
    assert snap.active_delve_dungeon == "grimvault"

    # ---- Step 4: RETREAT_TO_HAMLET(victory, wounded_boss=False) ----
    out = await drive_retreat(handler, outcome="victory", wounded_boss=False)
    assert not _errors(out), out
    hubs = _hub_views(out)
    assert len(hubs) == 1, f"expected HUB_VIEW after first retreat; got {out}"

    ws = _read_world_save(tmp_path, slug)
    assert ws.delve_count == 1
    assert ws.latest_delve_sin == "pride"
    assert ws.dungeon_wounds == {}
    assert len(ws.wall) == 1
    entry = ws.wall[0]
    assert entry.dungeon == "grimvault"
    assert entry.sin == "pride"
    assert entry.outcome == "victory"
    assert entry.wounded_boss is False

    # Post-retreat snapshot is hub-mode so the next DUNGEON_SELECT is legal.
    snap = handler._room.snapshot
    assert snap is not None
    assert snap.active_delve_dungeon is None

    # ---- Step 5: recruit a third hireling ----
    r3 = rest.post(f"/api/games/{slug}/hub/recruit")
    assert r3.status_code == 200, r3.text
    h3_id = r3.json()["id"]
    assert h3_id not in {h1_id, h2_id}

    ws = _read_world_save(tmp_path, slug)
    assert len(ws.roster) == 3
    # latest_delve_sin survives across recruits (only delve-end touches it).
    assert ws.latest_delve_sin == "pride"

    # ---- Step 6: DUNGEON_SELECT(horden, [the new one]) — second delve ----
    out = await drive_dungeon_select(
        handler, dungeon="horden", party_hireling_ids=[h3_id]
    )
    errs = _errors(out)
    assert not errs, (
        f"second DUNGEON_SELECT failed (the gnarly post-retreat case): "
        f"{[getattr(getattr(e, 'payload', None), 'message', '') for e in errs]}"
    )
    snap = handler._room.snapshot
    assert snap is not None
    assert snap.active_delve_dungeon == "horden"
    # latest_delve_sin doesn't update until this delve ends.
    ws = _read_world_save(tmp_path, slug)
    assert ws.latest_delve_sin == "pride"

    # ---- Step 7: RETREAT_TO_HAMLET(victory, wounded_boss=True) ----
    out = await drive_retreat(handler, outcome="victory", wounded_boss=True)
    assert not _errors(out), out
    hubs = _hub_views(out)
    assert len(hubs) == 1, f"expected HUB_VIEW after second retreat; got {out}"

    ws = _read_world_save(tmp_path, slug)
    assert ws.delve_count == 2
    assert ws.latest_delve_sin == "greed"
    assert ws.dungeon_wounds == {"horden": True}
    assert len(ws.wall) == 2
    entry2 = ws.wall[1]
    assert entry2.dungeon == "horden"
    assert entry2.sin == "greed"
    assert entry2.outcome == "victory"
    assert entry2.wounded_boss is True

    # The HUB_VIEW emitted by the retreat must already reflect horden.wounded=True.
    payload2 = hubs[0].payload  # type: ignore[attr-defined]
    by_slug = {d.slug: d for d in payload2.available_dungeons}
    assert by_slug["horden"].wounded is True
    assert by_slug["grimvault"].wounded is False
    assert by_slug["mawdeep"].wounded is False
