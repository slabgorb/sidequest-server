"""Unit tests for sidequest.dungeon.setpieces (schema only — Plan 4)."""

import pytest
from pydantic import ValidationError

from sidequest.dungeon.setpieces import (
    ComponentSlot,
    QuestComponent,
    SaveOrDie,
    SetPiece,
    SlotOption,
    TropeComponent,
)


def test_slot_option_requires_positive_weight():
    o = SlotOption(value="collapsing_floor", weight=2.0)
    assert o.value == "collapsing_floor"
    assert o.weight == 2.0


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_slot_option_rejects_nonpositive_weight(bad):
    with pytest.raises(ValidationError):
        SlotOption(value="x", weight=bad)


def test_slot_option_rejects_blank_value():
    with pytest.raises(ValidationError):
        SlotOption(value="   ", weight=1.0)


def test_slot_option_default_weight_is_one():
    assert SlotOption(value="x").weight == 1.0


def test_component_slot_requires_at_least_one_option():
    with pytest.raises(ValidationError, match="at least one option"):
        ComponentSlot(name="layout", options=[])


def test_component_slot_rejects_blank_name():
    with pytest.raises(ValidationError):
        ComponentSlot(name=" ", options=[SlotOption(value="x")])


def test_trope_component_requires_nonblank_id():
    t = TropeComponent(trope_id="priest_demands_a_sacrifice", params={"victims": 1})
    assert t.trope_id == "priest_demands_a_sacrifice"
    assert t.params == {"victims": 1}
    with pytest.raises(ValidationError):
        TropeComponent(trope_id="")


def test_quest_component_requires_nonblank_id_and_defaults_empty_params():
    q = QuestComponent(quest_id="recover_the_drowned_ledger")
    assert q.quest_id == "recover_the_drowned_ledger"
    assert q.params == {}
    with pytest.raises(ValidationError):
        QuestComponent(quest_id="  ")


def test_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        SlotOption(value="x", weight=1.0, typo=True)  # type: ignore[call-arg]


def _minimal_setpiece(**over) -> SetPiece:
    base = dict(
        id="false_floor",
        name="The False Floor",
        telegraph="A seam of newer mortar rings hollow underfoot.",
        outcome="The slab drops; anyone on it falls onto upturned stakes.",
        depth_band={"min": 0.0, "max": 60.0},
        slots=[
            {"name": "layout", "options": [{"value": "ten_foot_pit"}]},
            {"name": "loot", "options": [{"value": "rotted_pack", "weight": 2.0}]},
        ],
        trope_components=[],
        quest_components=[],
    )
    base.update(over)
    return SetPiece.model_validate(base)


def test_setpiece_minimal_valid():
    sp = _minimal_setpiece()
    assert sp.id == "false_floor"
    assert sp.save_or_die is None
    assert sp.depth_band.min == 0.0 and sp.depth_band.max == 60.0
    assert [s.name for s in sp.slots] == ["layout", "loot"]


@pytest.mark.parametrize("field", ["id", "telegraph", "outcome", "name"])
def test_setpiece_rejects_blank_mandatory_text(field):
    with pytest.raises(ValidationError):
        _minimal_setpiece(**{field: "   "})


def test_setpiece_save_or_die_is_inert_reference_data():
    sp = _minimal_setpiece(save_or_die={"save": "reflex", "dc": 15})
    assert isinstance(sp.save_or_die, SaveOrDie)
    assert sp.save_or_die.save == "reflex" and sp.save_or_die.dc == 15


def test_save_or_die_rejects_blank_save_and_nonpositive_dc():
    with pytest.raises(ValidationError):
        SaveOrDie(save="", dc=10)
    with pytest.raises(ValidationError):
        SaveOrDie(save="reflex", dc=0)


def test_setpiece_depth_band_inverted_rejected():
    with pytest.raises(ValidationError, match="max .* >= .* min"):
        _minimal_setpiece(depth_band={"min": 90.0, "max": 30.0})


def test_setpiece_carries_trope_and_quest_components():
    sp = _minimal_setpiece(
        trope_components=[{"trope_id": "priest_demands_a_sacrifice"}],
        quest_components=[{"quest_id": "seal_the_breach", "params": {"days": 3}}],
    )
    assert sp.trope_components[0].trope_id == "priest_demands_a_sacrifice"
    assert sp.quest_components[0].params == {"days": 3}


def test_setpiece_duplicate_slot_names_rejected():
    with pytest.raises(ValidationError, match="duplicate component slot"):
        _minimal_setpiece(
            slots=[
                {"name": "layout", "options": [{"value": "a"}]},
                {"name": "layout", "options": [{"value": "b"}]},
            ]
        )
