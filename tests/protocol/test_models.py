"""Tests for nested model types in sidequest.protocol.models.

Ported from sidequest-protocol/src/tests.rs and the narration/map/party
sub-test coverage. These tests exercise the nested types in isolation before
the GameMessage round-trips in test_messages.py exercise them end-to-end.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sidequest.protocol.models import (
    AbilityDefinition,
    AbilitySource,
    CellularParams,
    CharacterSheetDetails,
    CharacterState,
    CreationChoice,
    DerivedRoomData,
    FactCategory,
    Footnote,
    InitialState,
    InventoryItem,
    InventoryPayload,
    ItemGained,
    PartyMember,
    RolledStat,
    StateDelta,
    TacticalGridPayload,
)
from sidequest.protocol.types import NonBlankString

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def nbs(s: str) -> NonBlankString:
    return NonBlankString.model_validate(s)


def make_character_state(**kwargs: object) -> CharacterState:
    """Construct CharacterState via model_validate to handle 'class' alias."""
    defaults: dict[str, object] = {
        "name": "Hero",
        "hp": 20,
        "max_hp": 20,
        "level": 1,
        "class": "Adventurer",
        "statuses": [],
        "inventory": [],
    }
    defaults.update(kwargs)
    return CharacterState.model_validate(defaults)


def make_party_member(**kwargs: object) -> PartyMember:
    """Construct PartyMember via model_validate to handle 'class' alias."""
    defaults: dict[str, object] = {
        "player_id": "p1",
        "name": "Alice",
        "current_hp": 20,
        "max_hp": 20,
        "statuses": [],
        "class": "Adventurer",
        "level": 1,
    }
    defaults.update(kwargs)
    return PartyMember.model_validate(defaults)


def make_inventory_item(**kwargs: object) -> InventoryItem:
    """Construct InventoryItem via model_validate to handle 'type' alias."""
    defaults: dict[str, object] = {
        "name": "Item",
        "type": "misc",
        "equipped": False,
        "quantity": 1,
        "description": "A thing",
    }
    defaults.update(kwargs)
    return InventoryItem.model_validate(defaults)


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
    with pytest.raises(ValidationError):
        Footnote.model_validate({"summary": "", "category": "Lore", "is_new": True})


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
    cs = make_character_state(
        name="Grok",
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
    cs = make_character_state(name="Hero", **{"class": "Ranger"})
    data = json.loads(cs.model_dump_json())
    assert "class" in data
    assert "class_" not in data
    assert data["class"] == "Ranger"


def test_character_state_deny_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CharacterState.model_validate(
            {
                "name": "Grok",
                "hp": 10,
                "max_hp": 20,
                "statuses": [],
                "inventory": [],
                "unknown_field": "bad",
            }
        )


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
    cs = make_character_state(
        name="Grok",
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
    with pytest.raises(ValidationError):
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
    with pytest.raises(ValidationError):
        InitialState.model_validate(
            {
                "characters": [],
                "location": "Start",
                "quests": {},
                "extra": "bad",
            }
        )


# ---------------------------------------------------------------------------
# CreationChoice
# ---------------------------------------------------------------------------


def test_creation_choice_basic() -> None:
    c = CreationChoice(label=nbs("Warrior"), description=nbs("Strong fighter"))
    assert str(c.label) == "Warrior"
    assert str(c.description) == "Strong fighter"


def test_creation_choice_blank_label_rejected() -> None:
    with pytest.raises(ValidationError):
        CreationChoice.model_validate({"label": "", "description": "desc"})


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
    item = make_inventory_item(
        name="Iron Sword",
        **{"type": "weapon"},
        equipped=True,
        quantity=1,
        description="A sturdy blade",
    )
    assert str(item.name) == "Iron Sword"
    assert item.item_type == "weapon"
    assert item.equipped is True
    assert item.quantity == 1


def test_inventory_item_serializes_type_not_item_type() -> None:
    """Wire format must use 'type', not 'item_type'."""
    item = make_inventory_item(
        name="Torch",
        **{"type": "consumable"},
        equipped=False,
        quantity=3,
        description="Provides light",
    )
    data = json.loads(item.model_dump_json())
    assert "type" in data
    assert "item_type" not in data
    assert data["type"] == "consumable"


def test_inventory_item_deny_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        InventoryItem.model_validate(
            {
                "name": "Sword",
                "type": "weapon",
                "equipped": False,
                "quantity": 1,
                "description": "A sword",
                "bogus": "field",
            }
        )


# ---------------------------------------------------------------------------
# InventoryPayload
# ---------------------------------------------------------------------------


def test_inventory_payload_basic() -> None:
    payload = InventoryPayload(
        items=[
            make_inventory_item(
                name="Iron Sword",
                **{"type": "weapon"},
                equipped=True,
                quantity=1,
                description="A sturdy blade",
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
        abilities=[
            AbilityDefinition(
                name="Power Strike",
                genre_description="A mighty blow.",
                mechanical_effect="+2 damage.",
                source=AbilitySource.Class,
            )
        ],
        backstory=nbs("A wandering fighter."),
        personality=nbs("Gruff"),
        pronouns=nbs("he/him"),
        equipment=["Iron Sword [equipped]"],
    )
    assert str(sheet.race) == "Orc"
    assert sheet.stats["strength"] == 16
    assert sheet.pronouns is not None
    assert str(sheet.pronouns) == "he/him"
    assert sheet.abilities[0].name == "Power Strike"


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
    with pytest.raises(ValidationError):
        CharacterSheetDetails.model_validate(
            {
                "race": "Elf",
                "stats": {},
                "abilities": [],
                "backstory": "A backstory",
                "personality": "Calm",
                "extra": "bad",
            }
        )


# ---------------------------------------------------------------------------
# PartyMember
# ---------------------------------------------------------------------------


def test_party_member_basic() -> None:
    member = make_party_member(
        player_id="p1",
        name="Alice",
        character_name="Kael",
        current_hp=20,
        max_hp=20,
        statuses=["blessed"],
        **{"class": "Ranger"},
        level=3,
        portrait_url=None,
        current_location="Town Square",
        sheet=None,
        inventory=None,
    )
    assert str(member.player_id) == "p1"
    assert str(member.name) == "Alice"
    assert member.current_location is not None
    assert str(member.current_location) == "Town Square"


def test_party_member_serializes_class_not_class_underscore() -> None:
    """Wire format must use 'class', not 'class_'."""
    member = make_party_member(player_id="p1", name="Bob", **{"class": "Warrior"})
    data = json.loads(member.model_dump_json())
    assert "class" in data
    assert "class_" not in data
    assert data["class"] == "Warrior"


def test_party_member_pre_chargen_has_no_sheet_or_inventory() -> None:
    member = make_party_member(
        player_id="p2",
        name="Player2",
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
    with pytest.raises(ValidationError):
        PartyMember.model_validate(
            {
                "player_id": "p1",
                "name": "Alice",
                "current_hp": 10,
                "max_hp": 10,
                "statuses": [],
                "class": "Ranger",
                "level": 1,
                "bogus": "field",
            }
        )


# ---------------------------------------------------------------------------
# TacticalGridPayload — ADR-096 cellular cavern shape
# ---------------------------------------------------------------------------


def test_tactical_grid_cavern_payload_basic() -> None:
    grid = TacticalGridPayload(
        room_id="mouth",
        room_name="The Mouth",
        room_type="cavern",
        mask="##.\n.##\n###",
        cavern_image_url="/genre/caverns_and_claudes/worlds/caverns_sunden/rooms/mouth.cavern.png",
        cell_size=28,
        cellular=CellularParams(size=(18, 18), seed=1042, density=0.55, cutoff=5, passes=4),
        derived=DerivedRoomData(
            floor_count=142,
            exits={"north": (9, 0), "east": (17, 9), "south": None, "west": None},
            pois=[(8, 8), (13, 6)],
        ),
        tokens=[],
        initiative=None,
    )
    assert grid.room_type == "cavern"
    assert grid.cellular is not None
    assert grid.cellular.seed == 1042


def test_tactical_grid_settlement_payload_basic() -> None:
    grid = TacticalGridPayload(
        room_id="confessional",
        room_name="The Confessional",
        room_type="settlement",
        mask=None,
        cavern_image_url=None,
        cell_size=None,
        cellular=None,
        derived=None,
        tokens=[],
        initiative=None,
    )
    assert grid.room_type == "settlement"
    assert grid.cavern_image_url is None


def test_tactical_grid_invalid_room_type_rejected() -> None:
    with pytest.raises(ValidationError):
        TacticalGridPayload.model_validate(
            {
                "room_id": "x",
                "room_name": "x",
                "room_type": "dungeon",
                "mask": None,
                "cavern_image_url": None,
                "cell_size": None,
                "cellular": None,
                "derived": None,
                "tokens": [],
                "initiative": None,
            }
        )
