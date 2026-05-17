from __future__ import annotations

import pytest

from sidequest.dungeon.seed_bootstrap import (
    ENTRANCE_ID,
    build_entrance_seed_graph,
    build_expansion_one_request,
    select_entrance_theme_id,
)


def _palette(theme_id: str = "sunden_threshold"):
    # Reuse the real Task-5 DungeonTheme builder (no mocking the dungeon layer).
    from tests.dungeon.test_materializer import _commit_palette

    return _commit_palette(theme_id)


def test_select_entrance_theme_id_picks_depth_zero_eligible_theme() -> None:
    palette = _palette("sunden_threshold")
    assert select_entrance_theme_id(palette) == "sunden_threshold"


def test_select_entrance_theme_id_is_deterministic_by_id() -> None:
    from sidequest.dungeon.themes import ThemePalette
    from tests.dungeon.test_materializer import _theme_with_set_piece

    palette = ThemePalette(
        themes={
            "zeta_pit": _theme_with_set_piece("zeta_pit"),
            "alpha_gate": _theme_with_set_piece("alpha_gate"),
        }
    )
    # Both eligible at depth 0.0 (DepthBand(min=0.0, max=None)); tie broken
    # by id → deterministic, reproducible (No Silent Fallbacks).
    assert select_entrance_theme_id(palette) == "alpha_gate"


def test_select_entrance_theme_id_raises_when_nothing_covers_surface() -> None:
    from sidequest.dungeon.themes import (
        Adjacency,
        DepthBand,
        DungeonTheme,
        InteriorSpec,
        NarratorFlavor,
        ThemePalette,
    )

    deep_only = DungeonTheme(
        id="deep_only",
        display_name="Deep Only",
        generator_class="organic",
        interior=InteriorSpec(algorithm="cellular", braid_ratio=0.0),
        depth_band=DepthBand(min=300.0, max=None),
        narrator=NarratorFlavor(register="grave", flavor="x"),
        adjacency=Adjacency(),
        set_pieces=[],
    )
    with pytest.raises(ValueError, match="no theme covers the surface entrance"):
        select_entrance_theme_id(ThemePalette(themes={"deep_only": deep_only}))


def test_build_entrance_seed_graph_has_only_entrance_at_expansion_zero() -> None:
    g = build_entrance_seed_graph("sunden_threshold")
    assert g.entrance_id == ENTRANCE_ID == "entrance"
    assert set(g.nodes) == {"entrance"}
    n = g.nodes["entrance"]
    assert n.id == "entrance"
    assert n.expansion_id == 0
    assert n.theme == "sunden_threshold"
    assert n.depth_score is None  # frozen to 0.0 by the commit stage, not here


def test_build_expansion_one_request_is_valid_expansion_one() -> None:
    req = build_expansion_one_request(campaign_seed=7)
    assert req.expansion_id == 1
    assert req.campaign_seed == 7
    assert req.frontier_edge.from_region_id == "entrance"
    assert req.frontier_edge.spawn_depth_score == 0.0
    assert req.attach_region_ids == ("entrance",)
    assert req.burst_magnitude == 3
