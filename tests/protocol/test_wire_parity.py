"""Wire-format parity tests for sidequest.protocol models.

Verifies that Python pydantic serialization matches Rust serde
skip_serializing_if semantics (ADR-082 Strategy Spec §3):
  - None fields are omitted  (Rust: Option::is_none)
  - Empty lists/strings that match their declared default are omitted
    (Rust: Vec::is_empty / String::is_empty)
  - Numeric/bool fields with default values are kept (Rust: no skip_serializing_if)
  - Required list fields (no declared default) are kept even when empty
  - Populated fields are always present

Works for both direct model serialization and nested serialization through
GameMessage (RootModel), which uses pydantic's C-level serializer.
"""

from __future__ import annotations

import json

from sidequest.protocol import (
    GameMessage,
    NarrationPayload,
    StateDelta,
)
from sidequest.protocol.messages import (
    NarrationEndPayload,
    NarrationMessage,
)
from sidequest.protocol.models import (
    CharacterState,
    ExploredLocation,
    Footnote,
    FactCategory,
)
from sidequest.protocol.types import NonBlankString


def nbs(s: str) -> NonBlankString:
    return NonBlankString.model_validate(s)


# ---------------------------------------------------------------------------
# NarrationPayload — state_delta and footnotes skipped when absent/empty
# ---------------------------------------------------------------------------


def test_narration_payload_omits_none_state_delta() -> None:
    """state_delta=None must be absent from the wire (Option::is_none parity)."""
    p = NarrationPayload(text=nbs("hello"))
    data = json.loads(p.model_dump_json())
    assert "state_delta" not in data
    assert "footnotes" not in data


def test_narration_payload_omits_empty_footnotes() -> None:
    """footnotes=[] must be absent from the wire (Vec::is_empty parity)."""
    p = NarrationPayload(text=nbs("hello"), footnotes=[])
    data = json.loads(p.model_dump_json())
    assert "footnotes" not in data


def test_narration_payload_includes_populated_state_delta() -> None:
    """Populated state_delta must be present and carry only its non-empty fields."""
    p = NarrationPayload(text=nbs("hello"), state_delta=StateDelta(location="tavern"))
    data = json.loads(p.model_dump_json())
    assert "state_delta" in data
    assert data["state_delta"]["location"] == "tavern"
    # StateDelta's own empty fields (characters, quests, items_gained) should be absent
    assert "characters" not in data["state_delta"]
    assert "quests" not in data["state_delta"]
    assert "items_gained" not in data["state_delta"]


def test_narration_payload_includes_non_empty_footnotes() -> None:
    """Non-empty footnotes must be present."""
    footnote = Footnote(
        marker=1,
        summary=nbs("Ancient shrine of forgotten gods"),
        category=FactCategory.Lore,
        is_new=True,
    )
    p = NarrationPayload(text=nbs("You see it."), footnotes=[footnote])
    data = json.loads(p.model_dump_json())
    assert "footnotes" in data
    assert len(data["footnotes"]) == 1
    assert data["footnotes"][0]["summary"] == "Ancient shrine of forgotten gods"


# ---------------------------------------------------------------------------
# NarrationPayload via GameMessage — serializer must apply through RootModel
# ---------------------------------------------------------------------------


def test_narration_payload_omits_none_state_delta_through_game_message() -> None:
    """skip_serializing_if must apply even when payload is nested in GameMessage."""
    msg = GameMessage(root=NarrationMessage(
        payload=NarrationPayload(text=nbs("hello")),
        player_id="server",
    ))
    data = json.loads(msg.model_dump_json())
    assert "state_delta" not in data["payload"]
    assert "footnotes" not in data["payload"]


# ---------------------------------------------------------------------------
# ExploredLocation — room-graph fields skipped when absent/empty
# ---------------------------------------------------------------------------


def _make_loc(**kwargs: object) -> ExploredLocation:
    """Construct ExploredLocation via model_validate (handles 'type' alias)."""
    defaults: dict[str, object] = {"name": "Cave", "type": ""}
    defaults.update(kwargs)
    return ExploredLocation.model_validate(defaults)


def test_explored_location_omits_empty_room_exits() -> None:
    """room_exits=[] must be absent (Vec::is_empty parity)."""
    loc = _make_loc()
    data = json.loads(loc.model_dump_json())
    assert "room_exits" not in data


def test_explored_location_omits_empty_room_type() -> None:
    """room_type='' must be absent (String::is_empty parity)."""
    loc = _make_loc()
    data = json.loads(loc.model_dump_json())
    assert "room_type" not in data


def test_explored_location_omits_none_size() -> None:
    """size=None must be absent (Option::is_none parity)."""
    loc = _make_loc()
    data = json.loads(loc.model_dump_json())
    assert "size" not in data


def test_explored_location_omits_none_tactical_grid() -> None:
    """tactical_grid=None must be absent (Option::is_none parity)."""
    loc = _make_loc()
    data = json.loads(loc.model_dump_json())
    assert "tactical_grid" not in data


def test_explored_location_keeps_numeric_defaults() -> None:
    """x=0, y=0, is_current_room=False are NOT skipped (no skip_serializing_if in Rust)."""
    loc = _make_loc()
    data = json.loads(loc.model_dump_json())
    # x and y are ints — kept even at zero
    assert data["x"] == 0
    assert data["y"] == 0
    # is_current_room is bool — kept even at False
    assert data["is_current_room"] is False


def test_explored_location_includes_populated_room_exits() -> None:
    """Non-empty room_exits must be present."""
    from sidequest.protocol.models import RoomExitInfo
    loc = _make_loc(
        name="Dungeon Corridor",
        **{"type": "dungeon"},
        room_exits=[RoomExitInfo.model_validate({
            "target": "chamber_1", "exit_type": "door"
        })],
        room_type="corridor",
    )
    data = json.loads(loc.model_dump_json())
    assert "room_exits" in data
    assert len(data["room_exits"]) == 1
    assert "room_type" in data
    assert data["room_type"] == "corridor"


# ---------------------------------------------------------------------------
# CharacterState — required lists always kept even when empty
# ---------------------------------------------------------------------------


def test_character_state_required_lists_kept_when_empty() -> None:
    """statuses and inventory are required fields — kept even when empty lists."""
    cs = CharacterState.model_validate({
        "name": "Grok",
        "hp": 20,
        "max_hp": 20,
        "class": "Fighter",
        "statuses": [],
        "inventory": [],
    })
    data = json.loads(cs.model_dump_json())
    # Required fields without defaults are always present
    assert "statuses" in data
    assert "inventory" in data
    assert data["statuses"] == []
    assert data["inventory"] == []


def test_character_state_omits_none_archetype_provenance() -> None:
    """archetype_provenance=None must be absent (Option::is_none parity)."""
    cs = CharacterState.model_validate({
        "name": "Hero",
        "hp": 20,
        "max_hp": 20,
        "class": "Ranger",
        "statuses": [],
        "inventory": [],
    })
    data = json.loads(cs.model_dump_json())
    assert "archetype_provenance" not in data


# ---------------------------------------------------------------------------
# NarrationEndPayload — empty payload serializes as {}
# ---------------------------------------------------------------------------


def test_narration_end_empty_payload_serializes_as_empty_object() -> None:
    """NarrationEndPayload with no state_delta must serialize to {}."""
    p = NarrationEndPayload()
    data = json.loads(p.model_dump_json())
    assert data == {}


def test_narration_end_with_state_delta_includes_it() -> None:
    """NarrationEndPayload with state_delta must include it."""
    p = NarrationEndPayload(state_delta=StateDelta(location="end_room"))
    data = json.loads(p.model_dump_json())
    assert "state_delta" in data
    assert data["state_delta"]["location"] == "end_room"
