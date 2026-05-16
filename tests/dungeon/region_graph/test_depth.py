"""Unit tests for sidequest.dungeon.region_graph.depth."""

import pytest

from sidequest.dungeon.region_graph.depth import DepthConfig


def test_depth_config_defaults():
    c = DepthConfig()
    assert c.depth_per_hop == 10.0
    assert c.jitter_max == 3.0
    assert c.bucket_size == 30.0  # §12 decision: 3 ordinary hops per "level"
    c.validate()  # defaults are self-consistent


@pytest.mark.parametrize(
    "kwargs, msg",
    [
        ({"depth_per_hop": 0.0}, "depth_per_hop must be > 0"),
        ({"depth_per_hop": -1.0}, "depth_per_hop must be > 0"),
        ({"jitter_max": -0.1}, "jitter_max must be >= 0"),
        ({"bucket_size": 9.9}, "bucket_size must be >= depth_per_hop"),
    ],
    ids=["hop-zero", "hop-negative", "jitter-negative", "bucket-below-hop"],
)
def test_depth_config_validate_rejects(kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        DepthConfig(**kwargs).validate()


def test_depth_config_bucket_equal_to_hop_is_valid():
    DepthConfig(depth_per_hop=10.0, bucket_size=10.0).validate()  # equality is allowed


def test_depth_config_zero_jitter_is_valid():
    DepthConfig(jitter_max=0.0).validate()  # jitter is optional (spec §5)
