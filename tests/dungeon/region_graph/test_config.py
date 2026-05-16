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


@pytest.mark.parametrize(
    "field",
    [
        "min_stitch_edges",
        "min_loops_into_explored",
        "min_hidden_edges",
        "min_shortcut_edges",
    ],
)
def test_validate_rejects_each_scalar_floor_below_one(field):
    with pytest.raises(ValueError, match=rf"{field} must be >= 1"):
        JaquaysConfig(**{field: 0}).validate()


def test_validate_rejects_nonpositive_shortcut_gain():
    with pytest.raises(ValueError, match="min_shortcut_gain must be >= 1"):
        JaquaysConfig(min_shortcut_gain=0).validate()


def test_validate_rejects_negative_connection_burst():
    with pytest.raises(ValueError, match="connection_burst must be >= 0"):
        JaquaysConfig(connection_burst=-1).validate()


def test_validate_rejects_inverted_region_range():
    with pytest.raises(ValueError, match="new_regions_per_expansion"):
        JaquaysConfig(new_regions_per_expansion=(6, 3)).validate()


def test_validate_rejects_region_range_lo_below_one():
    with pytest.raises(ValueError, match=r"new_regions_per_expansion must be \(lo>=1, hi>=lo\)"):
        JaquaysConfig(new_regions_per_expansion=(0, 5)).validate()


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
    assert "min_shortcut_edges" in msg
    assert "no_single_chokepoint" in msg


def test_expansion_generation_error_copies_failing_list():
    src = ["a", "b"]
    err = ExpansionGenerationError(expansion_id=1, attempts=3, failing=src)
    src.append("c")
    assert err.failing == ["a", "b"]
