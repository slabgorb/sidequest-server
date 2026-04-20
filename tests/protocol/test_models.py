"""Tests for nested model types in sidequest.protocol.models.

Ported from sidequest-protocol/src/tests.rs and the narration/map/party
sub-test coverage. These tests exercise the nested types in isolation before
the GameMessage round-trips in test_messages.py exercise them end-to-end.
"""

from __future__ import annotations

import json

import pytest

from sidequest.protocol.models import (
    CartographyMetadata,
    CartographyRegion,
    CartographyRoute,
    CharacterSheetDetails,
    CharacterState,
    CreationChoice,
    ExploredLocation,
    FactCategory,
    FogBounds,
    Footnote,
    InitialState,
    InventoryItem,
    InventoryPayload,
    ItemGained,
    PartyMember,
    RolledStat,
    RoomExitInfo,
    StateDelta,
    TacticalFeaturePayload,
    TacticalGridPayload,
)
from sidequest.protocol.types import NonBlankString


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def nbs(s: str) -> NonBlankString:
    return NonBlankString.model_validate(s)


# ---------------------------------------------------------------------------
# FactCategory
# ---------------------------------------------------------------------------


def test_fact_category_lore() -> None:
    assert FactCategory.Lore == "Lore"


def test_fact_category_place() -> None:
    assert FactCategory.Place == "Place"


def test_fact_category_person() -> None:
    assert FactCategory.Person == "Person"


def test_fact_category_quest() -> None:
    assert FactCategory.Quest == "Quest"


def test_fact_category_ability() -> None:
    assert FactCategory.Ability == "Ability"


def test_fact_category_has_five_variants() -> None:
    assert len(FactCategory) == 5


def test_fact_category_invalid_rejected() -> None:
    with pytest.raises(ValueError):
        FactCategory("Bogus")


# ---------------------------------------------------------------------------
# Footnote
# ---------------------------------------------------------------------------


def test_footnote_basic_construction() -> None:
    f = Footnote(
        marker=1,
        summary=nbs("A hooded figure watches from the shadows"),
        category=FactCategory.Person,
        is_new=True,
    )
    assert f.marker == 1
    assert str(f.summary) == "A hooded figure watches from the shadows"
    assert f.category == FactCategory.Person
    assert f.is_new is True
    assert f.fact_id is None


def test_footnote_optional_marker_none() -> None:
    f = Footnote(
        summary=nbs("Ancient ruins of Khar"),
        category=FactCategory.Place,
        is_new=False,
    )
    assert f.marker is None


def test_footnote_callback_has_fact_id() -> None:
    f = Footnote(
        marker=2,
        fact_id="fact-abc-123",
        summary=nbs("The old tavern again"),
        category=FactCategory.Place,
        is_new=False,
    )
    assert f.fact_id == "fact-abc-123"
    assert f.is_new is False


def test_footnote_blank_summary_rejected() -> None:
    with pytest.raises((ValueError, Exception)):
        Footnote(
            summary=nbs(""),  # type: ignore[arg-type]
            category=FactCategory.Lore,
            is_new=True,
        )


# ---------------------------------------------------------------------------
# ItemGained
# ---------------------------------------------------------------------------


def test_item_gained_basic() -> None:
    item = ItemGained(
        name=nbs("sealed matte-black case"),
        description=nbs("A hard-sided case with an electronic lock"),
        category="tool",
    )
    assert str(item.name) == "sealed matte-black case"
    assert item.category == "tool"


def test_item_gained_default_description() -> None:
    item = ItemGained(name=nbs("mysterious orb"))
    assert str(item.description) == "An item found during adventure."


def test_item_gained_default_category() -> None:
    item = ItemGained(name=nbs("coin"))
    assert item.category == "misc"


# ---------------------------------------------------------------------------
# CharacterState
# ---------------------------------------------------------------------------


def test_character_state_basic() -> None:
    cs = CharacterState(
        name=nbs("Grok"),
        hp=15,
        max_hp=20,
        level=3,
        **{"class": "Fighter"},
        statuses=["poisoned"],
        inventory=["sword"],
    )
    assert str(cs.name) == "Grok"
    assert cs.hp == 15
    assert cs.max_hp == 20
    assert cs.level == 3
    assert cs.statuses == ["poisoned"]
    assert cs.inventory == ["sword"]
    assert cs.archetype_provenance is None


def test_character_state_serializes_class_not_class_underscore() -> None:
    """Wire format must use 'class', not 'class_'."""
    cs = CharacterState(
        name=nbs("Hero"),
        hp=20,
        max_hp=20,
        statuses=[],
        inventory=[],
        **{"class": "Ranger"},
    )
    data = json.loads(cs.model_dump_json())
    assert "class" in data
    assert "class_" not in data
    assert data["class"] == "Ranger"


def test_character_state_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        CharacterState.model_validate({
            "name": "Grok",
            "hp": 10,
            "max_hp": 20,
            "statuses": [],
            "inventory": [],
            "unknown_field": "bad",
        })


# ---------------------------------------------------------------------------
# StateDelta
# ---------------------------------------------------------------------------


def test_state_delta_all_optional() -> None:
    delta = StateDelta()
    assert delta.location is None
    assert delta.characters is None
    assert delta.quests is None
    assert delta.items_gained is None


def test_state_delta_with_location() -> None:
    delta = StateDelta(location="Dark Cave")
    assert delta.location == "Dark Cave"


def test_state_delta_with_characters() -> None:
    cs = CharacterState(
        name=nbs("Grok"),
        hp=15,
        max_hp=20,
        statuses=["poisoned"],
        inventory=["sword"],
    )
    delta = StateDelta(characters=[cs])
    assert delta.characters is not None
    assert len(delta.characters) == 1
    assert str(delta.characters[0].name) == "Grok"


def test_state_delta_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        StateDelta.model_validate({"bogus": "field"})


# ---------------------------------------------------------------------------
# InitialState
# ---------------------------------------------------------------------------


def test_initial_state_basic() -> None:
    state = InitialState(
        characters=[],
        location=nbs("Town Square"),
        quests={},
        turn_count=0,
    )
    assert str(state.location) == "Town Square"
    assert state.turn_count == 0


def test_initial_state_default_turn_count() -> None:
    state = InitialState(
        characters=[],
        location=nbs("Start"),
        quests={},
    )
    assert state.turn_count == 0


def test_initial_state_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        InitialState.model_validate({
            "characters": [],
            "location": "Start",
            "quests": {},
            "extra": "bad",
        })


# ---------------------------------------------------------------------------
# CreationChoice
# ---------------------------------------------------------------------------


def test_creation_choice_basic() -> None:
    c = CreationChoice(label=nbs("Warrior"), description=nbs("Strong fighter"))
    assert str(c.label) == "Warrior"
    assert str(c.description) == "Strong fighter"


def test_creation_choice_blank_label_rejected() -> None:
    with pytest.raises((ValueError, Exception)):
        CreationChoice(label=nbs(""), description=nbs("desc"))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RolledStat
# ---------------------------------------------------------------------------


def test_rolled_stat_basic() -> None:
    rs = RolledStat(name="STR", value=16)
    assert rs.name == "STR"
    assert rs.value == 16


# ---------------------------------------------------------------------------
# InventoryItem
# ---------------------------------------------------------------------------


def test_inventory_item_basic() -> None:
    item = InventoryItem(
        name=nbs("Iron Sword"),
        **{"type": "weapon"},
        equipped=True,
        quantity=1,
        description=nbs("A sturdy blade"),
    )
    assert str(item.name) == "Iron Sword"
    assert item.item_type == "weapon"
    assert item.equipped is True
    assert item.quantity == 1


def test_inventory_item_serializes_type_not_item_type() -> None:
    """Wire format must use 'type', not 'item_type'."""
    item = InventoryItem(
        name=nbs("Torch"),
        **{"type": "consumable"},
        equipped=False,
        quantity=3,
        description=nbs("Provides light"),
    )
    data = json.loads(item.model_dump_json())
    assert "type" in data
    assert "item_type" not in data
    assert data["type"] == "consumable"


def test_inventory_item_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        InventoryItem.model_validate({
            "name": "Sword",
            "type": "weapon",
            "equipped": False,
            "quantity": 1,
            "description": "A sword",
            "bogus": "field",
        })


# ---------------------------------------------------------------------------
# InventoryPayload
# ---------------------------------------------------------------------------


def test_inventory_payload_basic() -> None:
    payload = InventoryPayload(
        items=[
            InventoryItem(
                name=nbs("Iron Sword"),
                **{"type": "weapon"},
                equipped=True,
                quantity=1,
                description=nbs("A sturdy blade"),
            )
        ],
        gold=150,
    )
    assert payload.gold == 150
    assert len(payload.items) == 1


# ---------------------------------------------------------------------------
# CharacterSheetDetails
# ---------------------------------------------------------------------------


def test_character_sheet_details_basic() -> None:
    sheet = CharacterSheetDetails(
        race=nbs("Orc"),
        stats={"strength": 16, "dexterity": 12},
        abilities=["Power Strike"],
        backstory=nbs("A wandering fighter."),
        personality=nbs("Gruff"),
        pronouns=nbs("he/him"),
        equipment=["Iron Sword [equipped]"],
    )
    assert str(sheet.race) == "Orc"
    assert sheet.stats["strength"] == 16
    assert sheet.pronouns is not None
    assert str(sheet.pronouns) == "he/him"


def test_character_sheet_optional_pronouns() -> None:
    sheet = CharacterSheetDetails(
        race=nbs("Human"),
        stats={},
        abilities=[],
        backstory=nbs("Unknown origins."),
        personality=nbs("Quiet"),
    )
    assert sheet.pronouns is None


def test_character_sheet_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        CharacterSheetDetails.model_validate({
            "race": "Elf",
            "stats": {},
            "abilities": [],
            "backstory": "A backstory",
            "personality": "Calm",
            "extra": "bad",
        })


# ---------------------------------------------------------------------------
# PartyMember
# ---------------------------------------------------------------------------


def test_party_member_basic() -> None:
    member = PartyMember(
        player_id=nbs("p1"),
        name=nbs("Alice"),
        character_name=nbs("Kael"),
        current_hp=20,
        max_hp=20,
        statuses=["blessed"],
        **{"class": "Ranger"},
        level=3,
        portrait_url=None,
        current_location=nbs("Town Square"),
        sheet=None,
        inventory=None,
    )
    assert str(member.player_id) == "p1"
    assert str(member.name) == "Alice"
    assert str(member.current_location) == "Town Square"  # type: ignore[arg-type]


def test_party_member_serializes_class_not_class_underscore() -> None:
    """Wire format must use 'class', not 'class_'."""
    member = PartyMember(
        player_id=nbs("p1"),
        name=nbs("Bob"),
        current_hp=10,
        max_hp=10,
        statuses=[],
        **{"class": "Warrior"},
        level=1,
    )
    data = json.loads(member.model_dump_json())
    assert "class" in data
    assert "class_" not in data
    assert data["class"] == "Warrior"


def test_party_member_pre_chargen_has_no_sheet_or_inventory() -> None:
    member = PartyMember(
        player_id=nbs("p2"),
        name=nbs("Player2"),
        current_hp=0,
        max_hp=0,
        statuses=[],
        **{"class": "Adventurer"},
        level=0,
    )
    data = json.loads(member.model_dump_json())
    # None fields should be excluded from serialization
    assert "sheet" not in data or data.get("sheet") is None
    assert "inventory" not in data or data.get("inventory") is None


def test_party_member_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        PartyMember.model_validate({
            "player_id": "p1",
            "name": "Alice",
            "current_hp": 10,
            "max_hp": 10,
            "statuses": [],
            "class": "Ranger",
            "level": 1,
            "bogus": "field",
        })


# ---------------------------------------------------------------------------
# FogBounds
# ---------------------------------------------------------------------------


def test_fog_bounds_basic() -> None:
    fb = FogBounds(width=500, height=400)
    assert fb.width == 500
    assert fb.height == 400


def test_fog_bounds_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        FogBounds.model_validate({"width": 100, "height": 100, "depth": 50})


# ---------------------------------------------------------------------------
# RoomExitInfo
# ---------------------------------------------------------------------------


def test_room_exit_info_basic() -> None:
    exit_info = RoomExitInfo(target=nbs("dungeon_corridor"), exit_type="door")
    assert str(exit_info.target) == "dungeon_corridor"
    assert exit_info.exit_type == "door"


def test_room_exit_info_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        RoomExitInfo.model_validate({
            "target": "room_a",
            "exit_type": "door",
            "secret": True,
        })


# ---------------------------------------------------------------------------
# ExploredLocation
# ---------------------------------------------------------------------------


def test_explored_location_basic() -> None:
    loc = ExploredLocation(
        id="dark_cave",
        name=nbs("Dark Cave"),
        x=100,
        y=200,
        **{"type": "dungeon"},
        connections=["Forest Path"],
    )
    assert loc.id == "dark_cave"
    assert str(loc.name) == "Dark Cave"
    assert loc.x == 100
    assert loc.location_type == "dungeon"


def test_explored_location_serializes_type_not_location_type() -> None:
    """Wire format must use 'type', not 'location_type'."""
    loc = ExploredLocation(
        name=nbs("Village"),
        **{"type": "town"},
    )
    data = json.loads(loc.model_dump_json())
    assert "type" in data
    assert "location_type" not in data
    assert data["type"] == "town"


def test_explored_location_defaults() -> None:
    loc = ExploredLocation(name=nbs("Somewhere"))
    assert loc.id == ""
    assert loc.x == 0
    assert loc.y == 0
    assert loc.is_current_room is False
    assert loc.tactical_grid is None


def test_explored_location_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        ExploredLocation.model_validate({
            "name": "Place",
            "bogus": "field",
        })


# ---------------------------------------------------------------------------
# TacticalGridPayload / TacticalFeaturePayload
# ---------------------------------------------------------------------------


def test_tactical_feature_payload_basic() -> None:
    feature = TacticalFeaturePayload(
        glyph="A",
        feature_type="cover",
        label=nbs("Barrel"),
        positions=[[2, 3], [3, 3]],
    )
    assert feature.glyph == "A"
    assert feature.feature_type == "cover"
    assert feature.positions == [[2, 3], [3, 3]]


def test_tactical_grid_payload_basic() -> None:
    grid = TacticalGridPayload(
        width=10,
        height=8,
        cells=[["floor"] * 10 for _ in range(8)],
        features=[],
    )
    assert grid.width == 10
    assert grid.height == 8
    assert len(grid.cells) == 8


def test_tactical_grid_deny_unknown_fields() -> None:
    with pytest.raises((ValueError, Exception)):
        TacticalGridPayload.model_validate({
            "width": 5,
            "height": 5,
            "cells": [["floor"] * 5 for _ in range(5)],
            "features": [],
            "extra": "bad",
        })


# ---------------------------------------------------------------------------
# CartographyMetadata / CartographyRegion / CartographyRoute
# ---------------------------------------------------------------------------


def test_cartography_region_basic() -> None:
    region = CartographyRegion(
        name=nbs("The Wastes"),
        description="Barren irradiated lands",
        adjacent=["scrapyard", "highway"],
    )
    assert str(region.name) == "The Wastes"
    assert region.adjacent == ["scrapyard", "highway"]


def test_cartography_route_basic() -> None:
    route = CartographyRoute(
        name=nbs("Dust Road"),
        description="A cracked highway",
        from_id="wastes",
        to_id="scrapyard",
    )
    assert str(route.name) == "Dust Road"
    assert route.from_id == "wastes"
    assert route.to_id == "scrapyard"


def test_cartography_metadata_basic() -> None:
    meta = CartographyMetadata(
        navigation_mode="region",
        starting_region="start_zone",
        regions={
            "start_zone": CartographyRegion(name=nbs("Start Zone")),
        },
        routes=[],
    )
    assert meta.navigation_mode == "region"
    assert "start_zone" in meta.regions


def test_cartography_metadata_defaults() -> None:
    meta = CartographyMetadata(navigation_mode="room_graph")
    assert meta.starting_region == ""
    assert meta.regions == {}
    assert meta.routes == []
