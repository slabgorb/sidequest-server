from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sidequest.dungeon import frontier_hook


@pytest.fixture(autouse=True)
def _restore_frontier_observers() -> Any:
    before = list(frontier_hook._OBSERVERS)
    try:
        yield
    finally:
        frontier_hook._OBSERVERS[:] = before


def _sqlite_store() -> Any:
    from sidequest.game.persistence import SqliteStore

    return SqliteStore.open_in_memory()


def _beneath_sunden_world_dir() -> Path:
    # tests/dungeon/<file> -> tests -> sidequest-server -> repo root;
    # sidequest-content is a SIBLING of sidequest-server (parents[3]),
    # matching the existing _BENEATH_SUNDEN_WORLD in test_materializer.py.
    return (
        Path(__file__).resolve().parents[3]
        / "sidequest-content/genre_packs/caverns_and_claudes/worlds/beneath_sunden"
    )


def _snapshot() -> Any:
    from sidequest.game.session import GameSnapshot

    return GameSnapshot(genre_slug="caverns_and_claudes", world_slug="beneath_sunden")


def _pack() -> Any:
    """Pack-shaped object carrying .tropes (duck type, dossier _attach_pack).

    The real shallowest depth-0 beneath_sunden entrance theme is
    ``drowned_cavern`` (the only theme whose depth_band covers 0.0;
    select_entrance_theme_id picks the sorted-min id). Its sole set-piece
    ``the_siphon`` declares one trope_component
    (``the_thing_that_followed_you_down``) and NO quest_components. We
    supply exactly that real id so materialize's attach stage resolves
    against a faithful pack — NOT a stub.
    """
    from tests.dungeon.test_materializer import _attach_pack

    return _attach_pack("the_thing_that_followed_you_down")


async def test_non_beneath_sunden_is_a_clean_noop() -> None:
    from sidequest.dungeon.session_integration import attach_dungeon_to_session

    handle = await attach_dungeon_to_session(
        store=_sqlite_store(),
        snapshot=_snapshot(),
        genre_pack=object(),
        genre_slug="space_opera",
        world_slug="some_world",
        world_dir=Path("/nonexistent"),
    )
    assert handle is None
    assert frontier_hook.registered_observer_count() == 0


async def test_detach_is_null_safe() -> None:
    from sidequest.dungeon.session_integration import detach_dungeon_from_session

    await detach_dungeon_from_session(None)  # must not raise


async def test_attach_seeds_and_registers_then_detach_unregisters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidequest.dungeon import session_integration
    from sidequest.dungeon.persistence import DungeonStore
    from tests.dungeon.test_materializer import _reflecting_sdk_client

    monkeypatch.setattr(
        session_integration, "build_llm_client", _reflecting_sdk_client
    )

    store = _sqlite_store()
    snap = _snapshot()
    handle = await session_integration.attach_dungeon_to_session(
        store=store,
        snapshot=snap,
        genre_pack=_pack(),
        genre_slug="caverns_and_claudes",
        world_slug="beneath_sunden",
        world_dir=_beneath_sunden_world_dir(),
    )
    assert handle is not None
    assert frontier_hook.registered_observer_count() == 1

    ds = DungeonStore(store.connection())
    assert ds.get_campaign_seed() is not None
    nodes = ds.load_map(entrance_id="entrance").nodes
    assert "entrance" in nodes and nodes["entrance"].expansion_id == 0
    assert any(n.expansion_id == 1 for n in nodes.values())

    await session_integration.detach_dungeon_from_session(handle)
    assert frontier_hook.registered_observer_count() == 0


async def test_attach_is_idempotent_reuses_persisted_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidequest.dungeon import session_integration
    from sidequest.dungeon.persistence import DungeonStore
    from tests.dungeon.test_materializer import _reflecting_sdk_client

    monkeypatch.setattr(
        session_integration, "build_llm_client", _reflecting_sdk_client
    )
    store = _sqlite_store()
    kw = dict(
        store=store,
        snapshot=_snapshot(),
        genre_pack=_pack(),
        genre_slug="caverns_and_claudes",
        world_slug="beneath_sunden",
        world_dir=_beneath_sunden_world_dir(),
    )
    h1 = await session_integration.attach_dungeon_to_session(**kw)
    await session_integration.detach_dungeon_from_session(h1)
    seed1 = DungeonStore(store.connection()).get_campaign_seed()
    map1 = sorted(DungeonStore(store.connection()).load_map(entrance_id="entrance").nodes)

    h2 = await session_integration.attach_dungeon_to_session(**dict(kw, snapshot=_snapshot()))
    await session_integration.detach_dungeon_from_session(h2)
    seed2 = DungeonStore(store.connection()).get_campaign_seed()
    map2 = sorted(DungeonStore(store.connection()).load_map(entrance_id="entrance").nodes)

    assert seed1 == seed2, "reopen must reuse the frozen campaign_seed"
    assert map1 == map2, "reopen must NOT re-seed (idempotent bootstrap)"
