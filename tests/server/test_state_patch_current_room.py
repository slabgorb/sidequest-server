"""current_room survives serialization roundtrip on Character and Npc.

Wiring proof for the narrator's state-patch surface: when the narrator
emits a state_patch updating a character's current_room, the field is
preserved through model_dump → model_validate (the same path the patch
applier uses).
"""

import pytest

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore
from sidequest.game.session import Npc


def _basic_creature_core(name="Rux"):
    return CreatureCore(
        name=name,
        description="freighter rat",
        personality="terse",
        level=1,
        xp=0,
    )


def _basic_character() -> Character:
    return Character(
        core=_basic_creature_core("Rux"),
        backstory="freighter rat",
        char_class="captain",
        race="human",
        pronouns="they/them",
    )


def test_character_current_room_defaults_none():
    c = _basic_character()
    assert c.current_room is None


def test_character_current_room_round_trips():
    c = _basic_character()
    c.current_room = "galley"
    blob = c.model_dump()
    restored = Character.model_validate(blob)
    assert restored.current_room == "galley"


def test_character_state_patch_sets_current_room():
    """Simulates the narrator path: blob arrives with current_room set."""
    c = _basic_character()
    blob = c.model_dump()
    blob["current_room"] = "cockpit"
    restored = Character.model_validate(blob)
    assert restored.current_room == "cockpit"


def test_npc_current_room_defaults_none():
    n = Npc(core=_basic_creature_core("Captain"))
    assert n.current_room is None
    assert n.location is None  # orthogonal — current_room is not location


def test_npc_current_room_round_trips():
    n = Npc(core=_basic_creature_core("Captain"))
    n.current_room = "cockpit"
    restored = Npc.model_validate(n.model_dump())
    assert restored.current_room == "cockpit"
