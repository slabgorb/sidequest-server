"""Pydantic validation for LocationEntity types (Story 54-2 / ADR-109).

Covers AC-1 (manifest model validation), AC-2 (MessageType.LOCATION_DESCRIPTION
enum + dispatch registration), and AC-8 (payload.overlays default empty).
Cross-field invariants (real_object SHOULD have a binding) are explicitly
deferred to Story 54-3's pf validate locations — the pydantic layer loads
authored content leniently.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.protocol.models import (
    EncounterLocationOverlay,
    LocationEntity,
    LocationEntityBinding,
)

# ---------------------------------------------------------------------------
# Task 1: LocationEntity + LocationEntityBinding + EncounterLocationOverlay
# ---------------------------------------------------------------------------


def test_real_object_entity_with_binding_validates():
    entity = LocationEntity(
        id="bar",
        label="the bar",
        tier="real_object",
        binding=LocationEntityBinding(kind="location_feature", ref="glenross_bar"),
        affordances=["lean_on", "order_drink"],
    )
    assert entity.provenance == "authored"
    assert entity.promoted_at_turn is None
    assert entity.promoted_canon is None
    assert entity.binding is not None
    assert entity.binding.kind == "location_feature"
    assert entity.affordances == ["lean_on", "order_drink"]


def test_flavor_only_entity_without_binding_validates():
    entity = LocationEntity(id="cobwebs", label="cobwebs", tier="flavor_only")
    assert entity.binding is None
    assert entity.affordances == []
    assert entity.tier == "flavor_only"


def test_real_object_without_binding_is_allowed_at_model_level():
    """Model-level allows it; cross-field invariant is enforced by 54-3 validator.

    Per ADR-109 §1 (three-tier manifest) + plan rationale: authored YAML loads leniently so a
    failed binding ref doesn't crash room load; pf validate locations
    (Story 54-3) catches the drift at author time.
    """
    entity = LocationEntity(id="bar", label="the bar", tier="real_object")
    assert entity.binding is None
    assert entity.tier == "real_object"


def test_yes_and_entity_supports_runtime_promotion_metadata():
    """yes_and entities carry promotion bookkeeping (Story 54-6 writes these)."""
    entity = LocationEntity(
        id="player_chair",
        label="the wobbly chair",
        tier="yes_and",
        provenance="yes_and_minted",
        promoted_at_turn=42,
        promoted_canon="A wobbly chair near the hearth.",
    )
    assert entity.provenance == "yes_and_minted"
    assert entity.promoted_at_turn == 42
    assert entity.promoted_canon == "A wobbly chair near the hearth."


def test_all_four_provenance_literals_accepted():
    """Per ADR-109 §1: authored | cookbook | yes_and_promoted | yes_and_minted."""
    for prov in ("authored", "cookbook", "yes_and_promoted", "yes_and_minted"):
        e = LocationEntity(id="x", label="x", tier="yes_and", provenance=prov)  # type: ignore[arg-type]
        assert e.provenance == prov


def test_all_five_binding_kinds_accepted():
    """Per ADR-109 §1: location_feature | npc | item | clue | scenario_clue."""
    for kind in ("location_feature", "npc", "item", "clue", "scenario_clue"):
        b = LocationEntityBinding(kind=kind, ref="some_ref")  # type: ignore[arg-type]
        assert b.kind == kind
        assert b.ref == "some_ref"


def test_all_three_tiers_accepted():
    """Per ADR-109 §1: real_object | yes_and | flavor_only."""
    for tier in ("real_object", "yes_and", "flavor_only"):
        e = LocationEntity(id="x", label="x", tier=tier)  # type: ignore[arg-type]
        assert e.tier == tier


def test_unknown_tier_rejected():
    with pytest.raises(ValidationError):
        LocationEntity(id="x", label="x", tier="nonsense")  # type: ignore[arg-type]


def test_unknown_binding_kind_rejected():
    with pytest.raises(ValidationError):
        LocationEntityBinding(kind="banana", ref="x")  # type: ignore[arg-type]


def test_unknown_provenance_rejected():
    with pytest.raises(ValidationError):
        LocationEntity(
            id="x",
            label="x",
            tier="yes_and",
            provenance="invented_yesterday",  # type: ignore[arg-type]
        )


def test_extra_field_on_entity_rejected():
    """model_config = {'extra': 'forbid'} per ADR-109 §1 (three-tier manifest)."""
    with pytest.raises(ValidationError):
        LocationEntity(id="x", label="x", tier="flavor_only", surprise="!")  # type: ignore[call-arg]


def test_extra_field_on_binding_rejected():
    with pytest.raises(ValidationError):
        LocationEntityBinding(kind="npc", ref="x", surprise="!")  # type: ignore[call-arg]


def test_blank_label_rejected():
    with pytest.raises(ValidationError):
        LocationEntity(id="x", label="", tier="flavor_only")


def test_blank_id_rejected():
    with pytest.raises(ValidationError):
        LocationEntity(id="", label="x", tier="flavor_only")


def test_blank_binding_ref_rejected():
    with pytest.raises(ValidationError):
        LocationEntityBinding(kind="npc", ref="")


def test_entity_model_dump_round_trips():
    """AC-1 round-trip: model_dump → model_validate produces equivalent."""
    entity = LocationEntity(
        id="bar",
        label="the bar",
        tier="real_object",
        binding=LocationEntityBinding(kind="location_feature", ref="glenross_bar"),
        affordances=["lean_on"],
        provenance="authored",
    )
    dumped = entity.model_dump()
    rebuilt = LocationEntity.model_validate(dumped)
    assert rebuilt == entity


def test_encounter_overlay_defaults():
    overlay = EncounterLocationOverlay(bound_room_id="glenross_pub")
    assert overlay.entity_delta == []
    assert overlay.prose_suffix == ""


def test_encounter_overlay_with_delta_and_suffix():
    overlay = EncounterLocationOverlay(
        bound_room_id="glenross_pub",
        entity_delta=[
            LocationEntity(
                id="overturned_table",
                label="an overturned table",
                tier="yes_and",
            ),
        ],
        prose_suffix="A chair lies in splinters by the door.",
    )
    assert len(overlay.entity_delta) == 1
    assert overlay.entity_delta[0].id == "overturned_table"
    assert "splinters" in overlay.prose_suffix


def test_overlay_extra_field_rejected():
    with pytest.raises(ValidationError):
        EncounterLocationOverlay(bound_room_id="x", whatever="no")  # type: ignore[call-arg]


def test_overlay_blank_bound_room_id_rejected():
    with pytest.raises(ValidationError):
        EncounterLocationOverlay(bound_room_id="")


# ---------------------------------------------------------------------------
# Task 4: LocationDescriptionPayload + LocationDescriptionMessage + enum
# ---------------------------------------------------------------------------


def test_location_description_payload_minimum():
    """AC-1: payload constructs with all required fields and accepts empties."""
    from sidequest.protocol.models import LocationDescriptionPayload

    payload = LocationDescriptionPayload(
        region_id="glenross_pub",
        prose="The pub door is ajar.",
        terrain="building",
        entities=[],
        overlays=[],
    )
    assert payload.region_id == "glenross_pub"
    assert payload.prose == "The pub door is ajar."
    assert payload.terrain == "building"
    assert payload.entities == []
    assert payload.overlays == []


def test_location_description_payload_defaults_terrain_to_none():
    from sidequest.protocol.models import LocationDescriptionPayload

    payload = LocationDescriptionPayload(region_id="x", prose="y")
    assert payload.terrain is None
    assert payload.entities == []
    assert payload.overlays == []


def test_location_description_payload_blank_region_id_rejected():
    from sidequest.protocol.models import LocationDescriptionPayload

    with pytest.raises(ValidationError):
        LocationDescriptionPayload(region_id="", prose="x")


def test_location_description_payload_extra_field_rejected():
    from sidequest.protocol.models import LocationDescriptionPayload

    with pytest.raises(ValidationError):
        LocationDescriptionPayload(region_id="x", prose="y", surprise="!")  # type: ignore[call-arg]


def test_location_description_payload_carries_typed_entities():
    """Entities arrive on the wire as LocationEntity instances after parse."""
    from sidequest.protocol.models import LocationDescriptionPayload

    payload = LocationDescriptionPayload(
        region_id="sunden_square",
        prose="A well at the centre.",
        entities=[
            LocationEntity(
                id="well",
                label="the well at the centre",
                tier="real_object",
                binding=LocationEntityBinding(kind="location_feature", ref="sunden_square_well"),
            ),
        ],
    )
    assert len(payload.entities) == 1
    assert payload.entities[0].id == "well"
    assert payload.entities[0].binding is not None


def test_overlay_summary_minimum():
    """AC-8: overlay summary type exists for the message contract; 54-7 fills it."""
    from sidequest.protocol.models import LocationDescriptionOverlaySummary

    summary = LocationDescriptionOverlaySummary(encounter_id="enc-1")
    assert summary.encounter_id == "enc-1"
    assert summary.prose_suffix == ""
    assert summary.entity_delta_count == 0


def test_overlay_summary_extra_field_rejected():
    from sidequest.protocol.models import LocationDescriptionOverlaySummary

    with pytest.raises(ValidationError):
        LocationDescriptionOverlaySummary(encounter_id="x", surprise="!")  # type: ignore[call-arg]


def test_messagetype_enum_has_location_description():
    """AC-2: enum entry exists with the literal string value."""
    from sidequest.protocol.enums import MessageType

    assert MessageType.LOCATION_DESCRIPTION.value == "LOCATION_DESCRIPTION"


def test_location_description_message_roundtrip():
    """AC-2: message type tag matches enum; payload serializes."""
    from sidequest.protocol.enums import MessageType
    from sidequest.protocol.messages import LocationDescriptionMessage
    from sidequest.protocol.models import LocationDescriptionPayload

    msg = LocationDescriptionMessage(
        payload=LocationDescriptionPayload(
            region_id="glenross_pub",
            prose="The pub door is ajar.",
            terrain="building",
            entities=[],
            overlays=[],
        ),
        player_id="",
    )
    assert msg.type == MessageType.LOCATION_DESCRIPTION
    dumped = msg.model_dump(mode="json")
    # Pin the wire format to the literal string — TypeScript clients
    # consume the JSON shape, never the python enum repr.
    assert dumped["type"] == "LOCATION_DESCRIPTION"
    assert dumped["payload"]["region_id"] == "glenross_pub"
    assert dumped["payload"]["overlays"] == []


def test_location_description_message_registered_in_dispatch():
    """AC-2: GameMessage discriminated union resolves LOCATION_DESCRIPTION
    → LocationDescriptionMessage.

    The actual dispatch pattern in this codebase is pydantic's tagged
    union (see messages.py `_Phase1Variant` and `GameMessage(RootModel)`),
    not a dict-style registry. Verify via wire round-trip:
    `GameMessage.model_validate({"type": "LOCATION_DESCRIPTION", ...})`
    must yield a LocationDescriptionMessage at `.root`.
    """
    from sidequest.protocol.messages import (
        GameMessage,
        LocationDescriptionMessage,
    )

    wire = {
        "type": "LOCATION_DESCRIPTION",
        "payload": {
            "region_id": "test_region",
            "prose": "Test prose.",
            "terrain": None,
            "entities": [],
            "overlays": [],
        },
        "player_id": "",
    }
    parsed = GameMessage.model_validate(wire)
    assert isinstance(parsed.root, LocationDescriptionMessage), (
        "LOCATION_DESCRIPTION must dispatch to LocationDescriptionMessage; "
        f"got {type(parsed.root).__name__}"
    )
    assert parsed.root.payload.region_id == "test_region"


def test_location_description_reexported_from_protocol_package():
    """The protocol package re-exports the new symbols for downstream callers."""
    from sidequest import protocol

    assert hasattr(protocol, "LocationEntity")
    assert hasattr(protocol, "LocationEntityBinding")
    assert hasattr(protocol, "EncounterLocationOverlay")
    assert hasattr(protocol, "LocationDescriptionPayload")
    assert hasattr(protocol, "LocationDescriptionMessage")
    assert hasattr(protocol, "LocationDescriptionOverlaySummary")
