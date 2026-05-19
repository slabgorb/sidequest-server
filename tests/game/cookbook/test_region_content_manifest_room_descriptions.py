"""RegionContentManifest carries room_descriptions[] (Story 55-1).

Covers AC-1 (GeneratedRoomDescription shape + invariants), AC-2
(manifest field default + back-compat), and AC-7's surface contract
(the manifest is the carrier the assembler populates).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.game.cookbook.models import (
    GeneratedRoomDescription,
    RegionContentManifest,
)
from sidequest.protocol.models import LocationEntity


def _minimal_manifest_kwargs() -> dict:
    return dict(
        race="ooze",
        cr_band="shallow",
        size_budget={"wandering_rolls": 1, "special_rooms": 0, "loot_rolls": 1},
        wandering_table=[],
        loot_table=[],
        special_rooms=[],
    )


# ---------------------------------------------------------------------------
# AC-2: RegionContentManifest.room_descriptions defaults to []
# ---------------------------------------------------------------------------


def test_manifest_defaults_room_descriptions_empty() -> None:
    manifest = RegionContentManifest(**_minimal_manifest_kwargs())
    assert manifest.room_descriptions == [], (
        "RegionContentManifest.room_descriptions must default to [] so "
        "legacy callers that build a manifest by hand remain valid."
    )


def test_manifest_accepts_room_descriptions() -> None:
    rd = GeneratedRoomDescription(
        room_id="region_42",
        description="A narrow chamber slick with damp.",
        entities=[
            LocationEntity(
                id="slick_floor",
                label="the slick floor",
                tier="flavor_only",
                provenance="cookbook",
            ),
        ],
    )
    manifest = RegionContentManifest(
        **_minimal_manifest_kwargs(),
        room_descriptions=[rd],
    )
    assert len(manifest.room_descriptions) == 1
    assert manifest.room_descriptions[0].room_id == "region_42"
    assert manifest.room_descriptions[0].entities[0].provenance == "cookbook"


# ---------------------------------------------------------------------------
# AC-1: GeneratedRoomDescription shape, extra=forbid, non-empty room_id
# ---------------------------------------------------------------------------


def test_generated_room_description_fields() -> None:
    rd = GeneratedRoomDescription(
        room_id="x",
        description="prose",
        entities=[],
    )
    assert rd.room_id == "x"
    assert rd.description == "prose"
    assert rd.entities == []


def test_generated_room_description_defaults_entities_empty() -> None:
    rd = GeneratedRoomDescription(room_id="x", description="prose")
    assert rd.entities == [], (
        "GeneratedRoomDescription.entities must default to [] so callers "
        "can construct a description-only entry without explicit entities="
    )


def test_generated_room_description_rejects_empty_room_id() -> None:
    """AC-1: non-empty room_id required (Field(min_length=1))."""
    with pytest.raises(ValidationError):
        GeneratedRoomDescription(
            room_id="",
            description="anything",
            entities=[],
        )


def test_generated_room_description_extra_field_rejected() -> None:
    """AC-1: model_config = {"extra": "forbid"} — typos/drift surface loudly."""
    with pytest.raises(ValidationError):
        GeneratedRoomDescription(  # type: ignore[call-arg]
            room_id="x",
            description="x",
            entities=[],
            surprise="!",
        )


def test_generated_room_description_round_trips_via_model_dump() -> None:
    """Producer/consumer share a contract — entities survive a JSON round-trip."""
    rd = GeneratedRoomDescription(
        room_id="region_42",
        description="prose",
        entities=[
            LocationEntity(
                id="echoing_pool",
                label="a black pool",
                tier="real_object",
                provenance="cookbook",
            ),
        ],
    )
    dumped = rd.model_dump(mode="json")
    restored = GeneratedRoomDescription.model_validate(dumped)
    assert restored.room_id == "region_42"
    assert restored.entities[0].id == "echoing_pool"
    assert restored.entities[0].provenance == "cookbook"


# ---------------------------------------------------------------------------
# AC-7 surface: assemble_region threads compose result onto manifest
# (full behaviour covered in test_compose_room_prose; this is the wiring
# proof at the manifest layer using the real beneath_sunden bundle.)
# ---------------------------------------------------------------------------


def test_assemble_region_populates_room_descriptions(bundle) -> None:
    """AC-7: assemble_region requires room_id= and attaches the
    composed GeneratedRoomDescription to manifest.room_descriptions[0]."""
    from sidequest.game.cookbook.assemble import assemble_region

    # Pick any look id present in the real bundle; the shape of looks is
    # list[LookDef] per CookbookBundle (loader.py).
    look_id = bundle.looks[0].id

    manifest = assemble_region(
        bundle,
        campaign_seed="campaign-seed-1",
        expansion_id="expansion-1",
        depth_score=0.1,
        burst_magnitude=1,
        look=look_id,
        is_first_band_entry=True,
        room_id="region_42",
    )

    assert len(manifest.room_descriptions) == 1, (
        "v1 (ADR-106): one region = one room — manifest carries exactly "
        "one GeneratedRoomDescription per assemble_region call."
    )
    rd = manifest.room_descriptions[0]
    assert rd.room_id == "region_42"
    assert rd.description, "composed prose must be non-empty"
    assert any(e.tier == "flavor_only" for e in rd.entities), (
        "At least one dressing-derived flavor_only entity must land in the manifest."
    )
    # Every cookbook-emitted entity carries provenance="cookbook" — the
    # seam ADR-100 / 54-6 promotion logic uses.
    assert all(e.provenance == "cookbook" for e in rd.entities), (
        "Every entity emitted by the cookbook path must carry "
        "provenance='cookbook'; got "
        f"{[(e.id, e.provenance) for e in rd.entities]}"
    )


def test_assemble_region_room_id_is_required(bundle) -> None:
    """AC-7: room_id is a REQUIRED keyword argument — callers cannot omit it."""
    from sidequest.game.cookbook.assemble import assemble_region

    look_id = bundle.looks[0].id
    with pytest.raises(TypeError):
        # Intentional missing room_id — must fail loudly, not silently
        # default to "" or similar (No Silent Fallbacks).
        assemble_region(  # type: ignore[call-arg]
            bundle,
            campaign_seed="c",
            expansion_id="e",
            depth_score=0.1,
            burst_magnitude=1,
            look=look_id,
            is_first_band_entry=True,
        )


def test_assemble_region_is_deterministic_for_same_room_id(bundle) -> None:
    """AC-3 / AC-7: identical (campaign_seed, expansion_id, room_id, look)
    inputs produce an identical GeneratedRoomDescription."""
    from sidequest.game.cookbook.assemble import assemble_region

    look_id = bundle.looks[0].id
    kwargs = dict(
        campaign_seed="seed-X",
        expansion_id="exp-X",
        depth_score=0.1,
        burst_magnitude=1,
        look=look_id,
        is_first_band_entry=True,
        room_id="region_99",
    )
    a = assemble_region(bundle, **kwargs)
    b = assemble_region(bundle, **kwargs)
    assert a.room_descriptions[0].model_dump() == b.room_descriptions[0].model_dump()
