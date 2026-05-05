"""Tests for sidequest.game.world_save.

Hub-world persistence — survives SqliteStore.init_session() reinit.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sidequest.game.world_save import Hireling, WallEntry


def test_hireling_defaults_active_zero_stress():
    h = Hireling(id="vol_1", name="Volga Stein", archetype="prig")
    assert h.stress == 0
    assert h.status == "active"
    assert h.recruited_at_delve == 0
    assert h.notes == ""


def test_hireling_status_validates_literal():
    with pytest.raises(ValidationError):
        Hireling(id="vol_1", name="x", archetype="x", status="ghost")  # type: ignore[arg-type]


def test_hireling_id_pattern_enforced():
    """Item 4a's recruit generator and items 5/6/7's narrator-zone
    consumers share this contract — locked at model boundary."""
    # Valid shapes
    Hireling(id="vol_1", name="x", archetype="x")
    Hireling(id="prig_a3f", name="x", archetype="x")
    # Invalid shapes — must fail loud, no silent normalization
    for bad in ("Vol_1", "1vol", "vol-1", "vol 1", "", "vol!"):
        with pytest.raises(ValidationError):
            Hireling(id=bad, name="x", archetype="x")


def test_wall_entry_required_fields():
    e = WallEntry(
        delve_number=1,
        sin="pride",
        dungeon="grimvault",
        party_hireling_ids=["a", "b"],
        outcome="victory",
        timestamp=datetime.now(tz=UTC),
    )
    assert e.delve_number == 1
    assert e.party_hireling_ids == ["a", "b"]
    assert e.wounded_boss is False  # default


def test_wall_entry_outcome_validates_literal():
    """Outcome is the party-fate literal — wounded_dungeon is NOT here.
    Wound status lives on the orthogonal ``wounded_boss`` bool so that
    e.g. a TPK-after-wound is recordable as ``outcome=defeat``,
    ``wounded_boss=True``."""
    with pytest.raises(ValidationError):
        WallEntry(
            delve_number=1,
            sin="pride",
            dungeon="grimvault",
            party_hireling_ids=[],
            outcome="wounded_dungeon",  # rejected — not a party-fate
            timestamp=datetime.now(tz=UTC),
        )


def test_wall_entry_wounded_boss_is_orthogonal_to_outcome():
    """All four (outcome, wounded_boss) combinations must construct."""
    for outcome in ("victory", "defeat", "retreat"):
        for wounded in (True, False):
            e = WallEntry(
                delve_number=1, sin="pride", dungeon="grimvault",
                party_hireling_ids=[], outcome=outcome,
                wounded_boss=wounded,
                timestamp=datetime.now(tz=UTC),
            )
            assert e.outcome == outcome
            assert e.wounded_boss is wounded
