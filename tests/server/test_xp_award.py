from __future__ import annotations

import pytest

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.session import GameSnapshot
from sidequest.server.dispatch.encounter_lifecycle import award_turn_xp


def _make_char(xp: int = 0) -> Character:
    core = CreatureCore(
        name="Rux",
        description="A stoic fighter",
        personality="stoic",
        inventory=Inventory(),
        xp=xp,
    )
    return Character(
        core=core,
        char_class="Fighter",
        race="Human",
        backstory="A wandering fighter",
    )


@pytest.fixture
def snap_with_char():
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.characters.append(_make_char())
    return snap


def test_award_out_of_combat_grants_10_xp(snap_with_char):
    award_turn_xp(snap_with_char, in_combat=False)
    assert snap_with_char.characters[0].core.xp == 10


def test_award_in_combat_grants_25_xp(snap_with_char):
    award_turn_xp(snap_with_char, in_combat=True)
    assert snap_with_char.characters[0].core.xp == 25


def test_award_accumulates(snap_with_char):
    """Per-turn award must add to existing XP, not replace."""
    snap_with_char.characters[0].core.xp = 100
    award_turn_xp(snap_with_char, in_combat=True)
    assert snap_with_char.characters[0].core.xp == 125


def test_award_no_character_is_noop():
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    assert snap.characters == []
    award_turn_xp(snap, in_combat=True)  # must not raise
    assert snap.characters == []
