"""Tests for beats_available_for: class_filter ∩ encounter_beat_choices ∩ resource gate."""

import pytest

from sidequest.game.beat_filter import beats_available_for
from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.rules import (
    BeatDef,
    BeatKind,
    ConfrontationDef,
    MetricDef,
)


def _beat(id_, *, class_filter=None):
    return BeatDef(
        id=id_, label=id_, kind=BeatKind.strike, stat_check="STR", class_filter=class_filter
    )


def _confrontation(beats):
    return ConfrontationDef(
        type="combat",
        label="C",
        category="combat",
        player_metric=MetricDef(name="m", starting=0, threshold=7),
        opponent_metric=MetricDef(name="m", starting=0, threshold=7),
        beats=beats,
    )


def _class(display_name, choices):
    return ClassDef(
        id=display_name.lower(),
        display_name=display_name,
        rpg_role="tank",
        jungian_default="warrior",
        prime_requisite="STR",
        minimum_score=9,
        kit_table=f"{display_name.lower()}_kit",
        flavor="-",
        encounter_beat_choices=choices,
    )


def test_universal_beats_visible_to_every_class():
    cd = _confrontation([_beat("attack")])
    fighter = _class("Fighter", ["attack"])
    out = beats_available_for(cd, fighter, spell_slots_remaining=0.0)
    assert [b.id for b in out] == ["attack"]


def test_class_filter_excludes_other_classes():
    cd = _confrontation([_beat("cleave", class_filter=["Fighter"])])
    mage = _class("Mage", ["cleave"])  # mage's whitelist is wrong but filter still excludes
    out = beats_available_for(cd, mage, spell_slots_remaining=0.0)
    assert out == []


def test_encounter_beat_choices_narrows_pool():
    cd = _confrontation([_beat("attack"), _beat("flee")])
    fighter = _class("Fighter", ["attack"])  # excludes flee
    out = beats_available_for(cd, fighter, spell_slots_remaining=0.0)
    assert [b.id for b in out] == ["attack"]


def test_cast_spell_filtered_when_no_slots():
    cd = _confrontation([_beat("cast_spell", class_filter=["Mage"])])
    mage = _class("Mage", ["cast_spell"])
    out = beats_available_for(cd, mage, spell_slots_remaining=0.0)
    assert out == []


def test_cast_spell_visible_when_slot_available():
    cd = _confrontation([_beat("cast_spell", class_filter=["Mage"])])
    mage = _class("Mage", ["cast_spell"])
    out = beats_available_for(cd, mage, spell_slots_remaining=1.0)
    assert [b.id for b in out] == ["cast_spell"]


def test_empty_encounter_beat_choices_raises():
    from sidequest.genre.error import PackError

    cd = _confrontation([_beat("attack")])
    fighter = _class("Fighter", [])
    with pytest.raises(PackError, match="empty encounter_beat_choices"):
        beats_available_for(cd, fighter, spell_slots_remaining=0.0)
