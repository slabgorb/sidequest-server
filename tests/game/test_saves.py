"""Tests for resolve_save and apply_spell_effect (B/X B26 save resolver)."""

import pytest

from sidequest.game.encounter import EncounterActor
from sidequest.game.saves import (
    SaveResult,
    apply_spell_effect,
    resolve_save,
)
from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.rules import SaveCategory, SavingThrowsTable
from sidequest.protocol.dice import RollOutcome


def _fighter() -> ClassDef:
    return ClassDef(
        id="fighter",
        display_name="Fighter",
        rpg_role="tank",
        jungian_default="hero",
        prime_requisite="STR",
        minimum_score=9,
        kit_table="fighter_kit",
        saving_throws=SavingThrowsTable(
            death_ray_or_poison=12,
            magic_wands=13,
            paralysis_or_stone=14,
            dragon_breath=15,
            rods_staves_spells=16,
        ),
    )


def _mage() -> ClassDef:
    return ClassDef(
        id="mage",
        display_name="Mage",
        rpg_role="caster",
        jungian_default="magician",
        prime_requisite="INT",
        minimum_score=9,
        kit_table="mage_kit",
        saving_throws=SavingThrowsTable(
            death_ray_or_poison=13,
            magic_wands=14,
            paralysis_or_stone=13,
            dragon_breath=16,
            rods_staves_spells=15,
        ),
    )


def _classes() -> dict[str, ClassDef]:
    return {"Fighter": _fighter(), "Mage": _mage()}


def _actor(name: str = "carl", wis: int = 10) -> EncounterActor:
    return EncounterActor(
        name=name,
        role="adventurer",
        side="player",
        per_actor_state={"stats": {"WIS": wis, "STR": 10, "DEX": 10}},
    )


class _DeterministicRng:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, lo: int, hi: int) -> int:
        v = self._values.pop(0)
        assert lo <= v <= hi, f"queued {v} outside requested range [{lo},{hi}]"
        return v


def test_resolve_save_mage_vs_spells_pass_on_total_eq_target():
    rng = _DeterministicRng([14])
    res = resolve_save(
        defender=_actor(wis=12),
        defender_class="Mage",
        pack_classes=_classes(),
        category=SaveCategory.rods_staves_spells,
        ability="WIS",
        threat_label="SLEEP",
        rng=rng,
    )
    assert res.target == 15
    assert res.roll == 14
    assert res.mod == 1
    assert res.total == 15
    assert res.shift == 0
    assert res.tier is RollOutcome.Tie


def test_resolve_save_mage_vs_spells_clear_success():
    rng = _DeterministicRng([20])
    res = resolve_save(
        defender=_actor(wis=10),
        defender_class="Mage",
        pack_classes=_classes(),
        category=SaveCategory.rods_staves_spells,
        ability="WIS",
        threat_label="SLEEP",
        rng=rng,
    )
    assert res.tier is RollOutcome.CritSuccess


def test_resolve_save_mage_vs_spells_nat1_critfail():
    rng = _DeterministicRng([1])
    res = resolve_save(
        defender=_actor(wis=18),
        defender_class="Mage",
        pack_classes=_classes(),
        category=SaveCategory.rods_staves_spells,
        ability="WIS",
        threat_label="SLEEP",
        rng=rng,
    )
    assert res.tier is RollOutcome.CritFail


def test_resolve_save_target_differs_by_class():
    rng_fighter = _DeterministicRng([10])
    rng_mage = _DeterministicRng([10])
    f = resolve_save(
        defender=_actor(wis=10),
        defender_class="Fighter",
        pack_classes=_classes(),
        category=SaveCategory.rods_staves_spells,
        ability="WIS",
        threat_label="SLEEP",
        rng=rng_fighter,
    )
    m = resolve_save(
        defender=_actor(wis=10),
        defender_class="Mage",
        pack_classes=_classes(),
        category=SaveCategory.rods_staves_spells,
        ability="WIS",
        threat_label="SLEEP",
        rng=rng_mage,
    )
    assert f.target == 16
    assert m.target == 15
    assert f.shift == m.shift - 1


def test_resolve_save_dragon_breath_ignores_ability():
    rng = _DeterministicRng([10])
    res = resolve_save(
        defender=_actor(wis=20),
        defender_class="Mage",
        pack_classes=_classes(),
        category=SaveCategory.dragon_breath,
        ability=None,
        threat_label="DRAGON BREATH",
        rng=rng,
    )
    assert res.mod == 0
    assert res.total == 10
    assert res.target == 16
    assert res.shift == -6
    assert res.tier is RollOutcome.Fail


def test_resolve_save_loud_fails_when_class_not_in_pack():
    rng = _DeterministicRng([10])
    with pytest.raises(KeyError, match="Druid"):
        resolve_save(
            defender=_actor(),
            defender_class="Druid",
            pack_classes=_classes(),
            category=SaveCategory.rods_staves_spells,
            ability="WIS",
            threat_label="SLEEP",
            rng=rng,
        )


def test_resolve_save_loud_fails_when_class_has_no_table():
    rng = _DeterministicRng([10])
    classes = {
        "Bard": ClassDef(
            id="bard",
            display_name="Bard",
            rpg_role="support",
            jungian_default="trickster",
            prime_requisite="CHA",
            minimum_score=9,
            kit_table="bard_kit",
        ),
    }
    with pytest.raises(ValueError, match="saving_throws"):
        resolve_save(
            defender=_actor(),
            defender_class="Bard",
            pack_classes=classes,
            category=SaveCategory.rods_staves_spells,
            ability="WIS",
            threat_label="SLEEP",
            rng=rng,
        )


def test_apply_spell_effect_negates_on_critsuccess_save():
    res = SaveResult(
        defender_actor="carl",
        category=SaveCategory.rods_staves_spells,
        target=15,
        roll=20,
        mod=0,
        total=20,
        shift=5,
        tier=RollOutcome.CritSuccess,
        threat_label="SLEEP",
    )
    outcome = apply_spell_effect(spell_effect="negates", save_tier=res.tier)
    assert outcome.applies_full_effect is False
    assert outcome.applies_status is False


def test_apply_spell_effect_negates_on_fail_full_effect():
    outcome = apply_spell_effect(spell_effect="negates", save_tier=RollOutcome.Fail)
    assert outcome.applies_full_effect is True


def test_apply_spell_effect_halves_on_success_quarters():
    success = apply_spell_effect(spell_effect="halves", save_tier=RollOutcome.Success)
    tie = apply_spell_effect(spell_effect="halves", save_tier=RollOutcome.Tie)
    fail = apply_spell_effect(spell_effect="halves", save_tier=RollOutcome.Fail)
    assert success.damage_multiplier < tie.damage_multiplier < fail.damage_multiplier
    assert fail.damage_multiplier == 1.0


def test_apply_spell_effect_none_always_full():
    outcome = apply_spell_effect(spell_effect="none", save_tier=None)
    assert outcome.applies_full_effect is True
