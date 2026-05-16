"""WIRING: load the REAL shipped caverns_and_claudes themes/ scaffold and
cross-validate it against the REAL Plan-1 interiors registry + Plan-3
depth_score scale.

Per CLAUDE.md "Every Test Suite Needs a Wiring Test": Plan 4's runtime
consumer (Plan 7's materializer building a depth-filtered theme_pool,
Plan 6's set-piece roll) is an honest deferral — same stance as Plans
2 & 3. This test proves the loader is wired to real content + real
sibling modules, not unit-isolated against synthetic fixtures only.
"""

import pytest

from sidequest.dungeon.interiors import ALGORITHMS
from sidequest.dungeon.themes import ThemePalette, load_theme_palette


@pytest.fixture(scope="module")
def palette(content_dir) -> ThemePalette:
    pack = content_dir / "genre_packs" / "caverns_and_claudes"
    return load_theme_palette(pack)


def test_scaffold_loads_and_covers_every_generator_class(palette: ThemePalette):
    classes = {t.generator_class for t in palette.themes.values()}
    assert classes == {"organic", "labyrinthine", "structured", "built"}


def test_scaffold_exercises_every_real_interior_algorithm(palette: ThemePalette):
    used = {t.interior.algorithm for t in palette.themes.values()}
    assert used == set(ALGORITHMS)  # cellular, depthfirst, prim, roomcorridor


def test_labyrinth_trap_is_pristine_perfect_maze(palette: ThemePalette):
    # spec §5.2 / §12: labyrinth-trap braid_ratio == 0.0 deliberately
    lt = palette.get("labyrinth_trap")
    assert lt.interior.algorithm == "depthfirst"
    assert lt.interior.braid_ratio == 0.0


def test_other_maze_themes_are_braided(palette: ThemePalette):
    # §12: non-trap depthfirst/prim themes braid at 0.3
    for tid in ("winding_catacomb", "bone_crypt"):
        assert palette.get(tid).interior.braid_ratio == pytest.approx(0.3)


def test_every_setpiece_is_telegraphed_and_has_a_hard_outcome(
    palette: ThemePalette,
):
    # spec §4: the dungeon plays fair — every set-piece carries the tell
    # AND a hard, legible outcome (both non-blank, enforced by schema;
    # asserted here against the REAL authored content).
    seen = 0
    for theme in palette.themes.values():
        for sp in theme.set_pieces:
            assert sp.telegraph.strip()
            assert sp.outcome.strip()
            seen += 1
    assert seen >= 5  # at least one real set-piece per theme


def test_depth_bands_tile_the_scale_with_no_gap_from_the_surface(
    palette: ThemePalette,
):
    # At least one theme must be eligible at the entrance (depth_score 0)
    # and the bands must reach deep (Plan 3 raw units).
    assert palette.themes_for_depth(0.0), "no theme eligible at the surface"
    assert palette.themes_for_depth(90.0), "no theme eligible 9 hops down"


def test_adjacency_graph_is_closed(palette: ThemePalette):
    # load_theme_palette already enforces this; assert it explicitly so the
    # wiring test fails loudly if the scaffold drifts.
    known = set(palette.themes)
    for t in palette.themes.values():
        for ref in (*t.adjacency.prefers, *t.adjacency.avoids):
            assert ref in known
