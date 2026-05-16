"""Unit tests for sidequest.dungeon.themes (schema + loader — Plan 4)."""

import pytest
from pydantic import ValidationError

from sidequest.dungeon.interiors import ALGORITHMS
from sidequest.dungeon.themes import (
    Adjacency,
    CreatureEntry,
    DepthBand,
    DungeonTheme,
    InteriorSpec,
    LootEntry,
    NarratorFlavor,
)


def test_interior_spec_accepts_every_real_algorithm():
    # WIRING: the schema validates against the REAL Plan-1 coordinator
    # registry, not a hard-coded copy.
    for algo in ALGORITHMS:
        spec = InteriorSpec(algorithm=algo, params={}, braid_ratio=0.0)
        assert spec.algorithm == algo


def test_interior_spec_rejects_unknown_algorithm():
    with pytest.raises(ValidationError, match="unknown interior algorithm"):
        InteriorSpec(algorithm="voronoi", params={}, braid_ratio=0.0)


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
def test_interior_spec_braid_ratio_out_of_range_rejected(bad):
    with pytest.raises(ValidationError, match="braid_ratio"):
        InteriorSpec(algorithm="depthfirst", braid_ratio=bad)


def test_interior_spec_braid_ratio_bounds_inclusive():
    assert InteriorSpec(algorithm="depthfirst", braid_ratio=0.0).braid_ratio == 0.0
    assert InteriorSpec(algorithm="depthfirst", braid_ratio=1.0).braid_ratio == 1.0


def test_interior_spec_defaults():
    s = InteriorSpec(algorithm="cellular")
    assert s.params == {} and s.braid_ratio == 0.0


def test_interior_spec_extra_forbidden():
    with pytest.raises(ValidationError):
        InteriorSpec(algorithm="cellular", oops=1)  # type: ignore[call-arg]


def _theme(**over) -> DungeonTheme:
    base = dict(
        id="bone_crypt",
        display_name="The Bone Crypt",
        generator_class="structured",
        interior={"algorithm": "prim", "braid_ratio": 0.3},
        depth_band={"min": 30.0, "max": 120.0},
        narrator={
            "register": "grave",
            "flavor": "Dry air, stacked femurs, dust that remembers names.",
            "motifs": ["ossuary", "silence"],
        },
        adjacency={"prefers": ["winding_catacomb"], "avoids": ["drowned_cavern"]},
        creature_table=[{"ref": "bone_drake", "weight": 1.0}],
        loot_table=[{"ref": "grave_silver", "weight": 2.0}],
        set_pieces=[
            {
                "id": "false_floor",
                "name": "The False Floor",
                "telegraph": "Newer mortar rings hollow underfoot.",
                "outcome": "The slab drops onto upturned stakes.",
                "depth_band": {"min": 30.0, "max": 120.0},
                "slots": [{"name": "layout", "options": [{"value": "ten_foot_pit"}]}],
            }
        ],
    )
    base.update(over)
    return DungeonTheme.model_validate(base)


def test_dungeon_theme_minimal_valid():
    t = _theme()
    assert t.id == "bone_crypt"
    assert t.interior.algorithm == "prim"
    assert isinstance(t.depth_band, DepthBand)
    assert t.set_pieces[0].telegraph.startswith("Newer mortar")


@pytest.mark.parametrize(
    "gen_class, algo",
    [
        ("organic", "cellular"),
        ("labyrinthine", "depthfirst"),
        ("structured", "prim"),
        ("built", "roomcorridor"),
    ],
)
def test_generator_class_must_match_algorithm_family(gen_class, algo):
    # spec §5.2 mapping table is enforced as a hard invariant
    t = _theme(generator_class=gen_class, interior={"algorithm": algo})
    assert t.generator_class == gen_class


def test_generator_class_mismatch_rejected():
    with pytest.raises(ValidationError, match="generator_class .* does not match"):
        _theme(generator_class="built", interior={"algorithm": "cellular"})


def test_adjacency_same_id_in_prefers_and_avoids_rejected():
    with pytest.raises(ValidationError, match="both prefers and avoids"):
        Adjacency(prefers=["x"], avoids=["x"])


def test_adjacency_blank_avoids_entry_rejected():
    # "flooded clusters" (spec §6) -> a theme may prefer adjacency to itself
    Adjacency(prefers=["drowned_cavern"], avoids=[])  # validated at palette level
    with pytest.raises(ValidationError, match="cannot avoid itself"):
        # self-avoidance is only detectable with the owning id; the model
        # rejects the trivially-nonsensical empty-string form here
        Adjacency(prefers=[], avoids=[" "])


def test_narrator_flavor_rejects_blank_motif():
    with pytest.raises(ValidationError, match="motif cannot be blank"):
        NarratorFlavor(register="grave", flavor="x", motifs=["ok", "  "])


def test_narrator_flavor_requires_nonblank_register_and_flavor():
    with pytest.raises(ValidationError):
        NarratorFlavor(register=" ", flavor="x")
    with pytest.raises(ValidationError):
        NarratorFlavor(register="grave", flavor="  ")


def test_creature_and_loot_entries_require_positive_weight_and_ref():
    CreatureEntry(ref="bone_drake", weight=1.0)
    LootEntry(ref="grave_silver", weight=1.0)
    with pytest.raises(ValidationError):
        CreatureEntry(ref="", weight=1.0)
    with pytest.raises(ValidationError):
        LootEntry(ref="x", weight=0.0)


def test_theme_blank_id_rejected():
    with pytest.raises(ValidationError):
        _theme(id="  ")
