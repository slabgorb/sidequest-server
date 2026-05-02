"""Tests for sidequest.game.session — GameSnapshot, WorldStatePatch, NpcPatch.

Includes the wiring test: GameSnapshot with a Character + StateDelta
round-trips through model_dump_json()/validate_json().
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.game.creature_core import CreatureCore, Inventory, placeholder_edge_pool
from sidequest.game.delta import compute_delta, snapshot
from sidequest.game.session import (
    GameSnapshot,
    NarrativeEntry,
    Npc,
    NpcPatch,
    WorldStatePatch,
)
from tests.game.test_character import make_test_character


def _make_snapshot() -> GameSnapshot:
    return GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="iron_mines",
        characters=[make_test_character()],
        location="The Upper Gallery",
        time_of_day="evening",
        atmosphere="tense silence",
        current_region="Ironhold",
        discovered_regions=["Ironhold"],
        quest_log={"Find the Warden": "active: search the lower mines"},
        active_stakes="escape before the collapse",
        lore_established=["The mines were sealed after the accident"],
    )


# ---------------------------------------------------------------------------
# GameSnapshot construction
# ---------------------------------------------------------------------------


def test_snapshot_defaults():
    s = GameSnapshot()
    assert s.genre_slug == ""
    assert s.characters == []
    assert s.location == ""
    assert s.player_dead is False


def test_snapshot_with_character():
    s = _make_snapshot()
    assert len(s.characters) == 1
    assert s.characters[0].core.name == "Thorn Ironhide"


# ---------------------------------------------------------------------------
# WIRING TEST: GameSnapshot + StateDelta round-trip
# ---------------------------------------------------------------------------


def test_gamesnapshot_with_character_and_delta_roundtrip():
    """Wiring test: GameSnapshot with a Character + StateDelta round-trips through JSON."""
    original = _make_snapshot()
    before_snap = snapshot(original)

    # Mutate state
    original.location = "The Lower Gallery"
    original.quest_log["Rescue the Miners"] = "active"

    after_snap = snapshot(original)
    delta = compute_delta(before_snap, after_snap)

    assert delta.location_changed()
    assert delta.new_location == "The Lower Gallery"
    assert delta.quest_log_changed()
    assert not delta.is_empty()

    # Round-trip the snapshot
    json_str = original.model_dump_json()
    restored = GameSnapshot.model_validate_json(json_str)

    assert restored.location == "The Lower Gallery"
    assert restored.genre_slug == "caverns_and_claudes"
    assert restored.characters[0].core.name == "Thorn Ironhide"
    assert "Rescue the Miners" in restored.quest_log


# ---------------------------------------------------------------------------
# apply_world_patch
# ---------------------------------------------------------------------------


def test_apply_patch_location():
    s = _make_snapshot()
    s.apply_world_patch(WorldStatePatch(location="The Deep Caverns"))
    assert s.location == "The Deep Caverns"


def test_apply_patch_atmosphere():
    s = _make_snapshot()
    s.apply_world_patch(WorldStatePatch(atmosphere="eerie"))
    assert s.atmosphere == "eerie"


def test_apply_patch_quest_updates():
    s = _make_snapshot()
    s.apply_world_patch(WorldStatePatch(quest_updates={"New Quest": "active"}))
    assert "New Quest" in s.quest_log


def test_apply_patch_discover_regions():
    s = _make_snapshot()
    s.apply_world_patch(WorldStatePatch(discover_regions=["Sunken Vale"]))
    assert "Sunken Vale" in s.discovered_regions


def test_apply_patch_dedup_regions():
    s = _make_snapshot()
    s.apply_world_patch(WorldStatePatch(discover_regions=["Ironhold"]))
    assert s.discovered_regions.count("Ironhold") == 1


def test_apply_patch_lore_extends():
    s = _make_snapshot()
    s.apply_world_patch(WorldStatePatch(lore_established=["A new lore entry"]))
    assert "A new lore entry" in s.lore_established
    assert "The mines were sealed after the accident" in s.lore_established


def test_apply_patch_hp_changes():
    s = _make_snapshot()
    before = s.characters[0].core.edge.current
    s.apply_world_patch(WorldStatePatch(hp_changes={"Thorn Ironhide": -3}))
    assert s.characters[0].core.edge.current == before - 3


def test_apply_patch_hp_floored_at_zero():
    s = _make_snapshot()
    s.apply_world_patch(WorldStatePatch(hp_changes={"Thorn Ironhide": -1000}))
    assert s.characters[0].core.edge.current == 0


def test_apply_patch_npc_attitudes():
    npc = Npc(
        core=CreatureCore(
            name="Grog",
            description="A surly bouncer",
            personality="Gruff",
            inventory=Inventory(),
            statuses=[],
            edge=placeholder_edge_pool(),
        ),
        disposition=0,
    )
    s = _make_snapshot()
    s.npcs.append(npc)
    s.apply_world_patch(WorldStatePatch(npc_attitudes={"Grog": 10}))
    assert s.npcs[0].disposition == 10


def test_apply_patch_npc_upsert_new():
    s = _make_snapshot()
    s.apply_world_patch(WorldStatePatch(npcs_present=[NpcPatch(name="Mira")]))
    assert any(n.core.name == "Mira" for n in s.npcs)


def test_apply_patch_npc_upsert_existing():
    npc = Npc(
        core=CreatureCore(
            name="Mira",
            description="An old woman",
            personality="Wise",
            inventory=Inventory(),
            statuses=[],
            edge=placeholder_edge_pool(),
        )
    )
    s = _make_snapshot()
    s.npcs.append(npc)
    s.apply_world_patch(
        WorldStatePatch(npcs_present=[NpcPatch(name="Mira", description="A young woman")])
    )
    mira = next(n for n in s.npcs if n.core.name == "Mira")
    assert mira.core.description == "A young woman"


def test_apply_patch_none_fields_unchanged():
    s = _make_snapshot()
    original_location = s.location
    s.apply_world_patch(WorldStatePatch(atmosphere="eerie"))
    assert s.location == original_location


# ---------------------------------------------------------------------------
# NpcPatch validation
# ---------------------------------------------------------------------------


def test_npc_patch_blank_name_rejected():
    with pytest.raises(ValidationError):
        NpcPatch(name="")


def test_npc_patch_whitespace_name_rejected():
    with pytest.raises(ValidationError):
        NpcPatch(name="   ")


# ---------------------------------------------------------------------------
# lowest_friendly_hp_ratio
# ---------------------------------------------------------------------------


def test_lowest_friendly_hp_ratio_full():
    s = _make_snapshot()
    assert s.lowest_friendly_hp_ratio() == 1.0


def test_lowest_friendly_hp_ratio_damaged():
    s = _make_snapshot()
    s.characters[0].core.edge.current = s.characters[0].core.edge.max // 2
    ratio = s.lowest_friendly_hp_ratio()
    assert 0.4 < ratio < 0.6


def test_lowest_friendly_hp_ratio_no_characters():
    s = GameSnapshot()
    assert s.lowest_friendly_hp_ratio() == 1.0


# ---------------------------------------------------------------------------
# NarrativeEntry
# ---------------------------------------------------------------------------


def test_narrative_entry_defaults():
    e = NarrativeEntry(author="narrator", content="The party enters the mines.")
    assert e.round == 0
    assert e.tags == []
    assert e.speaker is None


def test_narrative_entry_roundtrip():
    e = NarrativeEntry(
        timestamp=123,
        round=5,
        author="narrator",
        content="Darkness closes in.",
        tags=["exploration"],
    )
    back = NarrativeEntry.model_validate_json(e.model_dump_json())
    assert back.content == "Darkness closes in."
    assert back.tags == ["exploration"]


# ---------------------------------------------------------------------------
# GameSnapshot JSON round-trip (full)
# ---------------------------------------------------------------------------


def test_full_snapshot_roundtrip():
    s = _make_snapshot()
    json_str = s.model_dump_json()
    back = GameSnapshot.model_validate_json(json_str)
    assert back.genre_slug == "caverns_and_claudes"
    assert back.characters[0].core.name == "Thorn Ironhide"
    assert back.quest_log == s.quest_log
    assert back.lore_established == s.lore_established
