import pytest

from sidequest.dungeon.region_graph.config import JaquaysConfig
from sidequest.dungeon.region_graph.errors import ExpansionGenerationError


def test_spec_defaults():
    c = JaquaysConfig()
    assert c.min_stitch_edges == 2
    assert c.min_loops_into_explored == 1
    assert c.min_hidden_edges == 1
    assert c.min_shortcut_edges == 1
    assert c.min_shortcut_gain == 3
    assert c.connection_burst == 3
    assert c.new_regions_per_expansion == (3, 6)
    assert c.max_reroll_attempts == 64
    assert c.edge_kinds == ("corridor", "stairs", "shaft", "chute", "secret")


def test_validate_rejects_empty_edge_kinds():
    with pytest.raises(ValueError, match="edge_kinds must be non-empty"):
        JaquaysConfig(edge_kinds=()).validate()


def test_validate_requires_secret_kind_for_hidden_edges():
    with pytest.raises(ValueError, match="edge_kinds must include 'secret'"):
        JaquaysConfig(edge_kinds=("corridor", "stairs")).validate()


def test_validate_rejects_floor_below_one():
    with pytest.raises(ValueError, match="min_stitch_edges must be >= 1"):
        JaquaysConfig(min_stitch_edges=0).validate()


def test_validate_rejects_inverted_region_range():
    with pytest.raises(ValueError, match="new_regions_per_expansion"):
        JaquaysConfig(new_regions_per_expansion=(6, 3)).validate()


def test_validate_rejects_too_few_regions_for_stitch_floor():
    with pytest.raises(ValueError, match="new_regions_per_expansion lower bound"):
        JaquaysConfig(min_stitch_edges=4, new_regions_per_expansion=(2, 5)).validate()


def test_validate_rejects_nonpositive_attempts():
    with pytest.raises(ValueError, match="max_reroll_attempts must be >= 1"):
        JaquaysConfig(max_reroll_attempts=0).validate()


def test_validate_passes_for_defaults():
    JaquaysConfig().validate()  # no raise


def test_expansion_generation_error_lists_failing_invariants():
    err = ExpansionGenerationError(
        expansion_id=7,
        attempts=64,
        failing=["min_shortcut_edges", "no_single_chokepoint"],
    )
    msg = str(err)
    assert "expansion 7" in msg
    assert "64 attempts" in msg
    assert "min_shortcut_edges" in msg and "no_single_chokepoint" in msg
