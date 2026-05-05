"""Tests for sidequest.game.world_save.

Hub-world persistence — survives SqliteStore.init_session() reinit.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sidequest.game.persistence import SaveSchemaIncompatibleError, SqliteStore
from sidequest.game.world_save import Hireling, WallEntry, WorldSave


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


def test_world_save_defaults_empty():
    ws = WorldSave()
    assert ws.roster == []
    assert ws.currency == 0
    assert ws.wall == []
    assert ws.dungeon_wounds == {}
    assert ws.latest_delve_sin is None
    assert ws.delve_count == 0
    assert ws.last_saved_at is None


def test_world_save_round_trip_json():
    ws = WorldSave(
        roster=[Hireling(id="vol_1", name="Volga", archetype="prig")],
        currency=42,
        dungeon_wounds={"grimvault": True},
        latest_delve_sin="pride",
        delve_count=3,
    )
    raw = ws.model_dump_json()
    ws2 = WorldSave.model_validate_json(raw)
    assert ws2.roster[0].name == "Volga"
    assert ws2.currency == 42
    assert ws2.dungeon_wounds == {"grimvault": True}
    assert ws2.latest_delve_sin == "pride"
    assert ws2.delve_count == 3


def test_load_world_save_empty_returns_default():
    store = SqliteStore.open_in_memory()
    ws = store.load_world_save()
    assert ws.roster == []
    assert ws.currency == 0
    assert ws.delve_count == 0


def test_load_world_save_invalid_json_raises():
    store = SqliteStore.open_in_memory()
    store._conn.execute(
        "INSERT INTO world_save (id, payload_json, saved_at) VALUES (1, ?, ?)",
        ("not json", "2026-05-05T00:00:00+00:00"),
    )
    store._conn.commit()
    with pytest.raises(SaveSchemaIncompatibleError):
        store.load_world_save()
