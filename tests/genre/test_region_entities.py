"""Region.entities[] parsing tests (Story 54-2 / ADR-109).

Covers AC-3: Region.entities defaults to [] and parses typed entities;
legacy untyped landmarks still loads on pre-54 worlds.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.genre.models.world import Region
from sidequest.protocol.models import LocationEntity


def test_region_with_no_entities_defaults_empty():
    """AC-3: graceful default — pre-54 cartography parses with [] entities."""
    region = Region(name="Glenross", summary="A village.", description="Quiet.")
    assert region.entities == []


def test_region_parses_typed_entities_from_dict_input():
    """AC-3: list-of-dict input is coerced into LocationEntity instances."""
    region = Region(
        name="Glenross",
        summary="A village.",
        description="The pub door is ajar.",
        entities=[
            {
                "id": "pub_door",
                "label": "the pub door",
                "tier": "real_object",
                "binding": {"kind": "location_feature", "ref": "glenross_pub_door"},
            },
            {"id": "cobwebs", "label": "cobwebs", "tier": "flavor_only"},
        ],
    )
    assert len(region.entities) == 2
    assert isinstance(region.entities[0], LocationEntity)
    assert isinstance(region.entities[1], LocationEntity)
    assert region.entities[0].tier == "real_object"
    assert region.entities[0].binding is not None
    assert region.entities[0].binding.kind == "location_feature"
    assert region.entities[1].tier == "flavor_only"
    assert region.entities[1].binding is None


def test_region_accepts_locationentity_instances_directly():
    """AC-3: callers can pass typed instances, not just dicts."""
    region = Region(
        name="x",
        summary="x",
        description="x",
        entities=[
            LocationEntity(id="well", label="the well", tier="flavor_only"),
        ],
    )
    assert len(region.entities) == 1
    assert isinstance(region.entities[0], LocationEntity)
    assert region.entities[0].id == "well"


def test_landmarks_field_still_accepted_for_backcompat():
    """AC-3: pre-54 worlds still load — legacy landmarks coexists with entities.

    Per the plan: content backfill happens in stories 54-4 (glenross) and
    54-5 (beneath_sunden). Until then, every pre-54 world must continue to
    load without modification.
    """
    region = Region(
        name="x",
        summary="x",
        description="x",
        landmarks=["the well", "the church"],
        entities=[{"id": "well", "label": "the well", "tier": "flavor_only"}],
    )
    assert region.landmarks == ["the well", "the church"]
    assert len(region.entities) == 1


def test_region_rejects_malformed_entity():
    """AC-3 negative: a bad entity dict surfaces a ValidationError up through Region.

    This is the noisy failure mode for malformed cartography — better
    than silently dropping the entry.
    """
    with pytest.raises(ValidationError):
        Region(
            name="x",
            summary="x",
            description="x",
            entities=[
                {"id": "x", "label": "x", "tier": "not_a_real_tier"},
            ],
        )
