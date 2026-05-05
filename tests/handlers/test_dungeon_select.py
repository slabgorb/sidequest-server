"""DUNGEON_SELECT handler — Sünden engine plan Task 8.

Locks the contract for the DUNGEON_SELECT inbound message: a hub-mode
session selects a dungeon + party, the server materializes the party
into ``GameSnapshot.characters``, sets ``active_delve_dungeon`` and a
delve-opening location, and the room becomes bound to the new
delve-mode snapshot.

Errors are typed (carry a ``code``) so the UI can render explanatory
text rather than guessing at the message string.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._helpers.delve import (
    drive_connect,
    drive_dismiss,
    drive_dungeon_select,
    drive_recruit,
    make_handler,
    seed_hub_game,
)

_GENRE = "caverns_and_claudes"
_HUB_WORLD = "caverns_three_sins"
_DUNGEON = "grimvault"

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


@pytest.mark.asyncio
async def test_dungeon_select_starts_delve(tmp_path: Path) -> None:
    slug = "test-delve-start"
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )
    h2 = drive_recruit(
        tmp_path, slug, hireling_id="brawler_002", name="Tor", archetype="brawler"
    )

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)

    outbound = await drive_dungeon_select(
        handler,
        dungeon=_DUNGEON,
        party_hireling_ids=[h1.id, h2.id],
    )

    types = [getattr(m, "type", None) for m in outbound]
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert not errors, (
        f"Expected no ERROR; got types={types}, "
        f"errors={[getattr(getattr(e, 'payload', None), 'message', '') for e in errors]}"
    )

    # Snapshot has been bound to the room with active_delve_dungeon set
    # and the party materialized into characters.
    snap = handler._room.snapshot
    assert snap is not None
    assert snap.active_delve_dungeon == _DUNGEON
    assert len(snap.characters) == 2
    char_hireling_ids = {c.hireling_id for c in snap.characters}
    assert char_hireling_ids == {h1.id, h2.id}


@pytest.mark.asyncio
async def test_dungeon_select_rejects_when_already_delving(tmp_path: Path) -> None:
    slug = "test-delve-already-active"
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)

    first = await drive_dungeon_select(
        handler, dungeon=_DUNGEON, party_hireling_ids=[h1.id]
    )
    assert not [m for m in first if getattr(m, "type", None) == "ERROR"]

    second = await drive_dungeon_select(
        handler, dungeon=_DUNGEON, party_hireling_ids=[h1.id]
    )
    errors = [m for m in second if getattr(m, "type", None) == "ERROR"]
    assert len(errors) == 1
    assert errors[0].payload.code == "delve_already_active"


@pytest.mark.asyncio
async def test_dungeon_select_rejects_unknown_dungeon(tmp_path: Path) -> None:
    slug = "test-delve-unknown"
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)

    outbound = await drive_dungeon_select(
        handler,
        dungeon="not_a_real_dungeon",
        party_hireling_ids=[h1.id],
    )
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert len(errors) == 1
    assert errors[0].payload.code == "unknown_dungeon"


@pytest.mark.asyncio
async def test_dungeon_select_rejects_dead_hireling(tmp_path: Path) -> None:
    slug = "test-delve-dead-hireling"
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )
    drive_dismiss(tmp_path, slug, hireling_id=h1.id, reason="died_offscreen")

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)

    outbound = await drive_dungeon_select(
        handler,
        dungeon=_DUNGEON,
        party_hireling_ids=[h1.id],
    )
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert len(errors) == 1
    assert errors[0].payload.code == "invalid_party"


