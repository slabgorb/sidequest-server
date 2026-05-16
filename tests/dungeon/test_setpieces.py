"""Unit tests for sidequest.dungeon.setpieces (schema only — Plan 4)."""

import pytest
from pydantic import ValidationError

from sidequest.dungeon.setpieces import (
    ComponentSlot,
    QuestComponent,
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
