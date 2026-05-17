from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sidequest.dungeon import frontier_hook

# The FULL set of distinct trope_ids referenced by set-pieces across ALL 5
# caverns_and_claudes themes (grepped from
# sidequest-content/genre_packs/caverns_and_claudes/themes/*.yaml):
#   bone_crypt      -> the_keeper_notices_the_disturbance
#   drowned_cavern  -> the_thing_that_followed_you_down
#   labyrinth_trap  -> the_resource_clock_you_can_see
#   sunless_temple  -> priest_demands_a_sacrifice  (+ quest deny_or_feed_the_altar)
#   winding_catacomb-> (no trope; quest find_the_unlit_way_out only)
# quest_components are NOT resolved against the pack (seed_quest_components has
# no quest registry — see sidequest/dungeon/setpiece_attach.py docstring), so
# only trope_ids must be supplied. Passing ALL real trope_ids makes the test
# robust to whichever themes the design/attach stages pick for expansion 1+2.
_ALL_REAL_TROPE_IDS = (
    "the_keeper_notices_the_disturbance",
    "the_thing_that_followed_you_down",
    "the_resource_clock_you_can_see",
    "priest_demands_a_sacrifice",
)


@pytest.fixture(autouse=True)
def _restore_frontier_observers() -> Any:
    """Prevent observer registration leaking into the ~6500-test suite
    (the Task-6/7 wiring-test fixture pattern)."""
    before = list(frontier_hook._OBSERVERS)
    try:
        yield
    finally:
        frontier_hook._OBSERVERS[:] = before


def _otel_in_memory() -> tuple[Any, Any, Any]:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider, provider.get_tracer("test")


def _beneath_sunden_world_dir() -> Path:
    # tests/dungeon/<file> -> tests -> sidequest-server -> repo root;
    # sidequest-content is a SIBLING of sidequest-server (parents[3]),
    # matching the existing _BENEATH_SUNDEN_WORLD in test_materializer.py.
    return (
        Path(__file__).resolve().parents[3]
        / "sidequest-content/genre_packs/caverns_and_claudes/worlds/beneath_sunden"
    )


async def test_session_lifecycle_registers_worker_and_dungeon_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: attach (fresh save) bootstraps expansion 0+1 and
    registers the worker; a REAL production region transition
    (snap.apply_world_patch -> frontier_hook.notify_region_transition)
    toward an unexpanded frontier edge materializes the next expansion;
    the frontier.region_transition span carries observers>=1 (the
    lie-detector signal flips off zero); detach unregisters cleanly."""
    import sidequest.telemetry.spans as _spans_module
    from sidequest.dungeon import session_integration
    from sidequest.dungeon.persistence import DungeonStore
    from sidequest.game.persistence import SqliteStore
    from sidequest.game.session import GameSnapshot, WorldStatePatch
    from sidequest.telemetry.spans.dungeon_materialize import (
        SPAN_FRONTIER_REGION_TRANSITION,
    )
    from tests.dungeon.test_materializer import _attach_pack, _reflecting_sdk_client

    monkeypatch.setattr(
        session_integration, "build_llm_client", _reflecting_sdk_client
    )

    store = SqliteStore.open_in_memory()
    snap = GameSnapshot(genre_slug="caverns_and_claudes", world_slug="beneath_sunden")
    snap.current_region = "entrance"

    exporter, _provider, real_tracer = _otel_in_memory()
    original_tracer_fn = _spans_module.tracer
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]

    handle = None
    try:
        handle = await session_integration.attach_dungeon_to_session(
            store=store,
            snapshot=snap,
            genre_pack=_attach_pack(*_ALL_REAL_TROPE_IDS),
            genre_slug="caverns_and_claudes",
            world_slug="beneath_sunden",
            world_dir=_beneath_sunden_world_dir(),
        )
        assert handle is not None
        assert frontier_hook.registered_observer_count() == 1, (
            "the look-ahead worker is not registered for the live session "
            "(observers=0 — the dungeon would not grow in a real game)"
        )

        ds = DungeonStore(store.connection())
        before = {n.expansion_id for n in ds.load_map(entrance_id="entrance").nodes.values()}
        assert before == {0, 1}, f"bootstrap did not seed expansion 0+1; got {before}"

        target = ds.load_frontier()[0].from_region_id
        assert target != "entrance", "frontier edge must leave entrance for the apply_world_patch != _prev_region guard to fire"
        snap.current_region = "entrance"
        snap.apply_world_patch(WorldStatePatch(current_region=target))
        await handle.drain()

        after = {n.expansion_id for n in ds.load_map(entrance_id="entrance").nodes.values()}
        assert max(after) >= 2, (
            f"region crossing toward an unexpanded frontier edge did NOT "
            f"materialize the next expansion; expansions={sorted(after)}"
        )

        finished = exporter.get_finished_spans()
        rt = [s for s in finished if s.name == SPAN_FRONTIER_REGION_TRANSITION]
        assert rt, "no frontier.region_transition span emitted (producer not fired)"
        assert any((s.attributes or {}).get("observers", 0) >= 1 for s in rt), (
            "every frontier.region_transition span has observers=0 — the "
            "seam fired but the session never registered a consumer (the "
            "ADR-106 lie-detector signal: dungeon does not grow)"
        )
    finally:
        await session_integration.detach_dungeon_from_session(handle)
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    assert frontier_hook.registered_observer_count() == 0, (
        "detach did not unregister the observer (registry leak across "
        "sessions)"
    )
