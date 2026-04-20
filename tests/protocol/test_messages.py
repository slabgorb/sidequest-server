"""Tests for Phase 1 payloads and GameMessage discriminated union.

Ported from:
- sidequest-protocol/src/tests.rs (message_type_tests, wire_compatibility_tests,
  deny_unknown_fields_tests)
- sidequest-protocol/src/tests.rs (player_location_tests)

Wire format verified: Rust uses #[serde(tag = "type")] with struct variants.
The top-level JSON object has "type", "payload", and "player_id" as siblings:
    {"type": "PLAYER_ACTION", "payload": {"action": "..."}, "player_id": ""}

GameMessage.model_validate_json() and model_dump_json() must round-trip
identically to the Rust serde output.
"""

from __future__ import annotations

import json

import pytest

from sidequest.protocol.enums import MessageType, NarratorVerbosity, NarratorVocabulary
from sidequest.protocol.messages import (
    ActionQueueMessage,
    ActionQueuePayload,
    CharacterCreationMessage,
    CharacterCreationPayload,
    ChapterMarkerMessage,
    ChapterMarkerPayload,
    ErrorMessage,
    ErrorPayload,
    GameMessage,
    MapUpdateMessage,
    MapUpdatePayload,
    NarrationEndMessage,
    NarrationEndPayload,
    NarrationMessage,
    NarrationPayload,
    PartyStatusMessage,
    PartyStatusPayload,
    PlayerActionMessage,
    PlayerActionPayload,
    SessionEventMessage,
    SessionEventPayload,
    ThinkingMessage,
    ThinkingPayload,
    TurnStatusMessage,
    TurnStatusPayload,
)
from sidequest.protocol.models import (
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
    StateDelta,
)
from sidequest.protocol.types import NonBlankString


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def nbs(s: str) -> NonBlankString:
    return NonBlankString.model_validate(s)


def round_trip(msg: GameMessage) -> GameMessage:
    """Serialize and re-parse a GameMessage."""
    json_str = msg.model_dump_json()
    return GameMessage.model_validate_json(json_str)


def parse_wire(json_str: str) -> GameMessage:
    """Parse a raw wire JSON string into GameMessage."""
    return GameMessage.model_validate_json(json_str)


# ===========================================================================
# AC: GameMessage — all 12 Phase 1 variants construct and round-trip
# Ported from tests.rs message_type_tests
# ===========================================================================


def test_player_action_round_trip() -> None:
    msg = GameMessage(root=PlayerActionMessage(
        payload=PlayerActionPayload(action=nbs("attack the goblin"), aside=False),
        player_id="player1",
    ))
    json_str = msg.model_dump_json()
    decoded = GameMessage.model_validate_json(json_str)
    assert decoded.type == MessageType.PLAYER_ACTION
    assert '"type":"PLAYER_ACTION"' in json_str
    assert str(decoded.payload.action) == "attack the goblin"  # type: ignore[union-attr]


def test_player_action_wire_type_tag() -> None:
    """Type discriminator must appear in serialized JSON."""
    msg = GameMessage(root=PlayerActionMessage(
        payload=PlayerActionPayload(action=nbs("go north"), aside=False),
        player_id="",
    ))
    data = json.loads(msg.model_dump_json())
    assert data["type"] == "PLAYER_ACTION"
    assert "payload" in data
    assert data["payload"]["action"] == "go north"
    assert data["player_id"] == ""


def test_narration_round_trip() -> None:
    msg = GameMessage(root=NarrationMessage(
        payload=NarrationPayload(text=nbs("The orc lunges...")),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.NARRATION
    assert '"type":"NARRATION"' in json_str


def test_narration_with_state_delta_round_trip() -> None:
    cs = CharacterState(
        name=nbs("Grok"),
        hp=15,
        max_hp=20,
        level=3,
        **{"class": "Fighter"},
        statuses=["poisoned"],
        inventory=["sword"],
    )
    delta = StateDelta(
        location="Dark Cave",
        characters=[cs],
        quests=None,
        items_gained=None,
    )
    msg = GameMessage(root=NarrationMessage(
        payload=NarrationPayload(text=nbs("You arrive."), state_delta=delta),
        player_id="",
    ))
    decoded = round_trip(msg)
    payload = decoded.payload
    assert payload.state_delta is not None  # type: ignore[union-attr]
    assert payload.state_delta.location == "Dark Cave"  # type: ignore[union-attr]
    assert str(payload.state_delta.characters[0].name) == "Grok"  # type: ignore[union-attr]


def test_narration_end_round_trip() -> None:
    msg = GameMessage(root=NarrationEndMessage(
        payload=NarrationEndPayload(state_delta=None),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.NARRATION_END
    assert '"type":"NARRATION_END"' in json_str


def test_thinking_round_trip() -> None:
    msg = GameMessage(root=ThinkingMessage(
        payload=ThinkingPayload(),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.THINKING
    assert '"type":"THINKING"' in json_str


def test_session_event_connect_round_trip() -> None:
    msg = GameMessage(root=SessionEventMessage(
        payload=SessionEventPayload(
            event="connect",
            player_name="Alice",
            genre="mutant_wasteland",
            world="flickering_reach",
        ),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.SESSION_EVENT
    assert '"type":"SESSION_EVENT"' in json_str
    assert decoded.payload.event == "connect"  # type: ignore[union-attr]


def test_session_event_ready_with_initial_state() -> None:
    cs = CharacterState(
        name=nbs("Hero"),
        hp=20,
        max_hp=20,
        level=1,
        **{"class": "Ranger"},
        statuses=[],
        inventory=["map"],
    )
    state = InitialState(
        characters=[cs],
        location=nbs("Town Square"),
        quests={},
        turn_count=0,
    )
    msg = GameMessage(root=SessionEventMessage(
        payload=SessionEventPayload(event="ready", initial_state=state),
        player_id="",
    ))
    decoded = round_trip(msg)
    payload = decoded.payload
    assert payload.initial_state is not None  # type: ignore[union-attr]
    assert str(payload.initial_state.location) == "Town Square"  # type: ignore[union-attr]


def test_character_creation_round_trip() -> None:
    msg = GameMessage(root=CharacterCreationMessage(
        payload=CharacterCreationPayload(
            phase="scene",
            scene_index=1,
            total_scenes=3,
            prompt="Describe your character...",
            choices=[
                CreationChoice(label=nbs("Warrior"), description=nbs("Strong fighter")),
            ],
            allows_freeform=True,
            input_type="text",
        ),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.CHARACTER_CREATION
    assert '"type":"CHARACTER_CREATION"' in json_str


def test_chargen_payload_deserializes_action_back() -> None:
    """The UI sends action:'back' when back button clicked. Must not be rejected."""
    wire = json.dumps({
        "type": "CHARACTER_CREATION",
        "payload": {"phase": "scene", "action": "back"},
        "player_id": "test-player",
    })
    msg = parse_wire(wire)
    assert msg.type == MessageType.CHARACTER_CREATION
    assert msg.payload.action == "back"  # type: ignore[union-attr]


def test_chargen_payload_deserializes_action_edit_with_target_step() -> None:
    """The UI sends action:'edit' + target_step from the review screen."""
    wire = json.dumps({
        "type": "CHARACTER_CREATION",
        "payload": {"phase": "confirmation", "action": "edit", "target_step": 2},
        "player_id": "test-player",
    })
    msg = parse_wire(wire)
    assert msg.payload.action == "edit"  # type: ignore[union-attr]
    assert msg.payload.target_step == 2  # type: ignore[union-attr]


def test_chargen_payload_without_action_still_deserializes() -> None:
    """Backwards compatibility: existing messages without action must still work."""
    wire = json.dumps({
        "type": "CHARACTER_CREATION",
        "payload": {"phase": "scene", "choice": "1"},
        "player_id": "test-player",
    })
    msg = parse_wire(wire)
    assert msg.type == MessageType.CHARACTER_CREATION
    assert msg.payload.action is None  # type: ignore[union-attr]


def test_turn_status_round_trip() -> None:
    msg = GameMessage(root=TurnStatusMessage(
        payload=TurnStatusPayload(player_name=nbs("Kael"), status="active"),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.TURN_STATUS
    assert '"type":"TURN_STATUS"' in json_str
    assert str(decoded.payload.player_name) == "Kael"  # type: ignore[union-attr]


def test_party_status_round_trip() -> None:
    """Collapsed PARTY_STATUS: sheet + inventory nested inside each PartyMember."""
    member = PartyMember(
        player_id=nbs("p1"),
        name=nbs("Player1"),
        character_name=nbs("Grok"),
        current_hp=20,
        max_hp=20,
        statuses=["blessed"],
        **{"class": "Warrior"},
        level=3,
        sheet=CharacterSheetDetails(
            race=nbs("Orc"),
            stats={"strength": 16, "dexterity": 12},
            abilities=["Power Strike"],
            backstory=nbs("A wandering fighter."),
            personality=nbs("Gruff"),
            pronouns=nbs("he/him"),
            equipment=["Iron Sword [equipped]"],
        ),
        inventory=InventoryPayload(
            items=[InventoryItem(
                name=nbs("Iron Sword"),
                **{"type": "weapon"},
                equipped=True,
                quantity=1,
                description=nbs("A sturdy blade"),
            )],
            gold=150,
        ),
    )
    msg = GameMessage(root=PartyStatusMessage(
        payload=PartyStatusPayload(members=[member]),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.PARTY_STATUS
    assert '"type":"PARTY_STATUS"' in json_str

    # Pre-chargen member: sheet and inventory are None
    pre_chargen = PartyMember(
        player_id=nbs("p2"),
        name=nbs("Player2"),
        current_hp=0,
        max_hp=0,
        statuses=[],
        **{"class": "Adventurer"},
        level=0,
    )
    pre_json = pre_chargen.model_dump_json()
    assert '"sheet"' not in pre_json or json.loads(pre_json).get("sheet") is None
    assert '"inventory"' not in pre_json or json.loads(pre_json).get("inventory") is None


def test_map_update_round_trip() -> None:
    loc = ExploredLocation(
        id="dark_cave",
        name=nbs("Dark Cave"),
        x=100,
        y=200,
        **{"type": "dungeon"},
        connections=["Forest Path"],
    )
    msg = GameMessage(root=MapUpdateMessage(
        payload=MapUpdatePayload(
            current_location=nbs("Dark Cave"),
            region=nbs("Shadowlands"),
            explored=[loc],
            fog_bounds=FogBounds(width=500, height=400),
        ),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.MAP_UPDATE
    assert '"type":"MAP_UPDATE"' in json_str


def test_chapter_marker_round_trip() -> None:
    msg = GameMessage(root=ChapterMarkerMessage(
        payload=ChapterMarkerPayload(title="Chapter 1", location="The Dark Forest"),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.CHAPTER_MARKER
    assert '"type":"CHAPTER_MARKER"' in json_str


def test_action_queue_round_trip() -> None:
    msg = GameMessage(root=ActionQueueMessage(
        payload=ActionQueuePayload(actions=[]),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.ACTION_QUEUE
    assert '"type":"ACTION_QUEUE"' in json_str


def test_error_round_trip() -> None:
    msg = GameMessage(root=ErrorMessage(
        payload=ErrorPayload(message=nbs("something went wrong")),
        player_id="",
    ))
    json_str = msg.model_dump_json()
    decoded = round_trip(msg)
    assert decoded.type == MessageType.ERROR
    assert '"type":"ERROR"' in json_str
    assert str(decoded.payload.message) == "something went wrong"  # type: ignore[union-attr]


# ===========================================================================
# AC: Wire compatibility — exact JSON shapes from api-contract.md
# Ported from tests.rs wire_compatibility_tests
# ===========================================================================


def test_player_action_wire_format() -> None:
    """Exact JSON from api-contract.md."""
    wire = json.dumps({
        "type": "PLAYER_ACTION",
        "payload": {"action": "attack the goblin", "aside": False},
        "player_id": "",
    })
    msg = parse_wire(wire)
    assert msg.type == MessageType.PLAYER_ACTION
    assert str(msg.payload.action) == "attack the goblin"  # type: ignore[union-attr]
    assert msg.payload.aside is False  # type: ignore[union-attr]
    assert msg.player_id == ""


def test_session_event_connect_wire_format() -> None:
    wire = json.dumps({
        "type": "SESSION_EVENT",
        "payload": {
            "event": "connect",
            "player_name": "Alice",
            "genre": "mutant_wasteland",
            "world": "flickering_reach",
        },
        "player_id": "",
    })
    msg = parse_wire(wire)
    assert msg.type == MessageType.SESSION_EVENT
    assert msg.payload.event == "connect"  # type: ignore[union-attr]
    assert msg.payload.player_name == "Alice"  # type: ignore[union-attr]


def test_thinking_wire_format() -> None:
    wire = json.dumps({"type": "THINKING", "payload": {}, "player_id": ""})
    msg = parse_wire(wire)
    assert msg.type == MessageType.THINKING


def test_narration_with_delta_wire_format() -> None:
    wire = json.dumps({
        "type": "NARRATION",
        "payload": {
            "text": "The orc lunges...",
            "state_delta": {
                "location": "Dark Cave",
                "characters": [
                    {"name": "Grok", "hp": 15, "max_hp": 20, "statuses": ["poisoned"], "inventory": ["sword"]},
                ],
                "quests": {"Find the Gem": "in_progress"},
            },
        },
        "player_id": "",
    })
    msg = parse_wire(wire)
    assert msg.type == MessageType.NARRATION
    payload = msg.payload
    assert str(payload.text) == "The orc lunges..."  # type: ignore[union-attr]
    delta = payload.state_delta  # type: ignore[union-attr]
    assert delta is not None
    assert delta.location == "Dark Cave"
    assert str(delta.characters[0].name) == "Grok"
    assert delta.characters[0].hp == 15


def test_error_wire_format() -> None:
    wire = json.dumps({
        "type": "ERROR",
        "payload": {"message": "something broke"},
        "player_id": "",
    })
    msg = parse_wire(wire)
    assert msg.type == MessageType.ERROR
    assert str(msg.payload.message) == "something broke"  # type: ignore[union-attr]


def test_unknown_message_type_rejected() -> None:
    """Unknown type string must fail deserialization."""
    wire = json.dumps({"type": "BOGUS_TYPE", "payload": {}, "player_id": ""})
    with pytest.raises((ValueError, Exception)):
        parse_wire(wire)


# ===========================================================================
# AC: deny_unknown_fields — payloads reject unexpected JSON keys
# Ported from tests.rs deny_unknown_fields_tests
# ===========================================================================


def test_player_action_rejects_extra_fields() -> None:
    wire = json.dumps({
        "type": "PLAYER_ACTION",
        "payload": {"action": "go north", "aside": False, "hacker_field": "gotcha"},
        "player_id": "",
    })
    with pytest.raises((ValueError, Exception)):
        parse_wire(wire)


def test_error_payload_rejects_extra_fields() -> None:
    wire = json.dumps({
        "type": "ERROR",
        "payload": {"message": "oops", "secret": "leak"},
        "player_id": "",
    })
    with pytest.raises((ValueError, Exception)):
        parse_wire(wire)


# ===========================================================================
# Narration ADR-076: NARRATION_CHUNK must not deserialize
# Ported from narration_collapse_story_27_9_tests.rs
# ===========================================================================


def test_narration_chunk_json_does_not_deserialize_as_game_message() -> None:
    """ADR-076: NARRATION_CHUNK variant no longer exists."""
    wire = '{"type":"NARRATION_CHUNK","payload":{"text":"partial"},"player_id":""}'
    with pytest.raises((ValueError, Exception)):
        parse_wire(wire)


# ===========================================================================
# Story 14-2: Player location on character sheet
# Ported from tests.rs player_location_tests
# ===========================================================================


def test_party_member_includes_current_location() -> None:
    member = PartyMember(
        player_id=nbs("p1"),
        name=nbs("Alice"),
        character_name=nbs("Kael"),
        current_hp=20,
        max_hp=20,
        statuses=[],
        **{"class": "Ranger"},
        level=3,
        current_location=nbs("The Rusty Cantina"),
    )
    assert str(member.current_location) == "The Rusty Cantina"  # type: ignore[arg-type]


def test_party_member_location_serializes_to_json() -> None:
    member = PartyMember(
        player_id=nbs("p1"),
        name=nbs("Alice"),
        character_name=nbs("Kael"),
        current_hp=20,
        max_hp=20,
        statuses=[],
        **{"class": "Ranger"},
        level=3,
        current_location=nbs("Market Square"),
    )
    data = json.loads(member.model_dump_json())
    assert data.get("current_location") == "Market Square"


def test_party_member_location_round_trips_through_json() -> None:
    member = PartyMember(
        player_id=nbs("p1"),
        name=nbs("Alice"),
        character_name=nbs("Kael"),
        current_hp=20,
        max_hp=20,
        statuses=[],
        **{"class": "Ranger"},
        level=3,
        current_location=nbs("The Wastes"),
    )
    json_str = member.model_dump_json()
    decoded = PartyMember.model_validate_json(json_str)
    assert str(decoded.current_location) == "The Wastes"  # type: ignore[arg-type]


def test_party_status_with_multiple_locations() -> None:
    """Multiplayer: two players in different locations."""
    members = [
        PartyMember(
            player_id=nbs("p1"),
            name=nbs("Alice"),
            character_name=nbs("Kael"),
            current_hp=20,
            max_hp=20,
            statuses=[],
            **{"class": "Ranger"},
            level=3,
            current_location=nbs("The Rusty Cantina"),
        ),
        PartyMember(
            player_id=nbs("p2"),
            name=nbs("Bob"),
            character_name=nbs("Lyra"),
            current_hp=35,
            max_hp=40,
            statuses=[],
            **{"class": "Cleric"},
            level=5,
            current_location=nbs("Scrapyard Gate"),
        ),
    ]
    msg = GameMessage(root=PartyStatusMessage(
        payload=PartyStatusPayload(members=members),
        player_id="p1",
    ))
    decoded = round_trip(msg)
    party_members = decoded.payload.members  # type: ignore[union-attr]
    assert str(party_members[0].current_location) == "The Rusty Cantina"
    assert str(party_members[1].current_location) == "Scrapyard Gate"


# ===========================================================================
# Footnote round-trip inside NarrationPayload
# Ported from narration_with_state_delta_round_trip pattern
# ===========================================================================


def test_narration_with_footnotes_round_trip() -> None:
    footnote = Footnote(
        marker=1,
        summary=nbs("A hooded figure watches from the rafters"),
        category=FactCategory.Person,
        is_new=True,
    )
    msg = GameMessage(root=NarrationMessage(
        payload=NarrationPayload(
            text=nbs("The innkeeper nods subtly[1]."),
            footnotes=[footnote],
        ),
        player_id="",
    ))
    decoded = round_trip(msg)
    payload = decoded.payload
    assert len(payload.footnotes) == 1  # type: ignore[union-attr]
    assert str(payload.footnotes[0].summary) == "A hooded figure watches from the rafters"  # type: ignore[union-attr]
    assert payload.footnotes[0].category == FactCategory.Person  # type: ignore[union-attr]


def test_narration_with_items_gained() -> None:
    delta = StateDelta(
        items_gained=[ItemGained(
            name=nbs("Rusty Key"),
            description=nbs("A key to an unknown door"),
            category="quest",
        )],
    )
    msg = GameMessage(root=NarrationMessage(
        payload=NarrationPayload(text=nbs("You find a key."), state_delta=delta),
        player_id="",
    ))
    decoded = round_trip(msg)
    items = decoded.payload.state_delta.items_gained  # type: ignore[union-attr]
    assert items is not None
    assert len(items) == 1
    assert str(items[0].name) == "Rusty Key"


# ===========================================================================
# GameMessage discriminator property
# ===========================================================================


def test_game_message_type_property() -> None:
    msg = GameMessage(root=ErrorMessage(
        payload=ErrorPayload(message=nbs("test")),
        player_id="",
    ))
    assert msg.type == MessageType.ERROR


def test_game_message_payload_property() -> None:
    msg = GameMessage(root=ThinkingMessage(
        payload=ThinkingPayload(),
        player_id="server",
    ))
    assert isinstance(msg.payload, ThinkingPayload)


def test_game_message_player_id_property() -> None:
    msg = GameMessage(root=PlayerActionMessage(
        payload=PlayerActionPayload(action=nbs("look around")),
        player_id="p1",
    ))
    assert msg.player_id == "p1"


# ===========================================================================
# All 12 Phase 1 types exist and discriminate correctly
# Wiring test: GameMessage resolves all 12 variant types
# ===========================================================================


def test_all_phase1_variants_parse_correctly() -> None:
    """Integration: all 12 Phase 1 variants must parse via GameMessage."""
    payloads = [
        (MessageType.PLAYER_ACTION, {"payload": {"action": "go north", "aside": False}}),
        (MessageType.NARRATION, {"payload": {"text": "You move forward."}}),
        (MessageType.NARRATION_END, {"payload": {}}),
        (MessageType.THINKING, {"payload": {}}),
        (MessageType.SESSION_EVENT, {"payload": {"event": "connect"}}),
        (MessageType.CHARACTER_CREATION, {"payload": {"phase": "scene"}}),
        (MessageType.TURN_STATUS, {"payload": {"player_name": "Alice", "status": "active"}}),
        (MessageType.PARTY_STATUS, {"payload": {"members": []}}),
        (MessageType.MAP_UPDATE, {"payload": {
            "current_location": "Village",
            "region": "Outlands",
            "explored": [],
        }}),
        (MessageType.CHAPTER_MARKER, {"payload": {}}),
        (MessageType.ACTION_QUEUE, {"payload": {"actions": []}}),
        (MessageType.ERROR, {"payload": {"message": "oops"}}),
    ]
    for msg_type, extra in payloads:
        wire = {"type": msg_type.value, "player_id": "", **extra}
        msg = GameMessage.model_validate(wire)
        assert msg.type == msg_type, f"Expected {msg_type}, got {msg.type}"
