"""Unit tests for sidequest.dungeon.themes (schema + loader — Plan 4)."""

import pytest
from pydantic import ValidationError

from sidequest.dungeon.interiors import ALGORITHMS
from sidequest.dungeon.themes import InteriorSpec


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
