"""Tests for SaveCategory enum and SavingThrowsTable model (B/X B26 port)."""

import pytest
from pydantic import ValidationError

from sidequest.genre.models.rules import SaveCategory, SavingThrowsTable


def test_save_category_enum_closed():
    assert {c.value for c in SaveCategory} == {
        "death_ray_or_poison",
        "magic_wands",
        "paralysis_or_stone",
        "dragon_breath",
        "rods_staves_spells",
    }


def _fighter_table() -> SavingThrowsTable:
    return SavingThrowsTable(
        death_ray_or_poison=12,
        magic_wands=13,
        paralysis_or_stone=14,
        dragon_breath=15,
        rods_staves_spells=16,
    )


def test_saving_throws_table_constructs():
    t = _fighter_table()
    assert t.dragon_breath == 15
    assert t.rods_staves_spells == 16


def test_saving_throws_table_target_for_lookup():
    t = _fighter_table()
    assert t.target_for(SaveCategory.dragon_breath) == 15
    assert t.target_for(SaveCategory.rods_staves_spells) == 16


def test_saving_throws_table_rejects_below_2():
    with pytest.raises(ValidationError, match="saving throw"):
        SavingThrowsTable(
            death_ray_or_poison=1,
            magic_wands=13,
            paralysis_or_stone=14,
            dragon_breath=15,
            rods_staves_spells=16,
        )


def test_saving_throws_table_rejects_above_20():
    with pytest.raises(ValidationError, match="saving throw"):
        SavingThrowsTable(
            death_ray_or_poison=12,
            magic_wands=13,
            paralysis_or_stone=14,
            dragon_breath=15,
            rods_staves_spells=21,
        )


def test_saving_throws_table_extra_forbid():
    with pytest.raises(ValidationError):
        SavingThrowsTable(
            death_ray_or_poison=12,
            magic_wands=13,
            paralysis_or_stone=14,
            dragon_breath=15,
            rods_staves_spells=16,
            crit_save=99,
        )
