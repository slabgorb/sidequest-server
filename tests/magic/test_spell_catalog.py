from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "cc_arcane_l1.yaml"


def test_spell_catalog_loads_three_spell_fixture():
    from sidequest.magic.spell_catalog import load_spell_catalog

    cat = load_spell_catalog(FIXTURE)
    assert cat.tradition == "arcane"
    assert cat.level == 1
    assert len(cat.spells) == 3
    spell_ids = {s.id for s in cat.spells}
    assert spell_ids == {"magic_missile", "sleep", "charm_person"}


def test_spell_catalog_lookup_by_id():
    from sidequest.magic.spell_catalog import load_spell_catalog

    cat = load_spell_catalog(FIXTURE)
    s = cat.get("magic_missile")
    assert s.name == "Magic Missile"
    assert s.save.stat is None
    assert s.range == "near"


def test_spell_catalog_lookup_missing_raises():
    from sidequest.magic.spell_catalog import load_spell_catalog

    cat = load_spell_catalog(FIXTURE)
    with pytest.raises(KeyError, match="firewing"):
        cat.get("firewing")


def _spell_dict(spell_id: str = "magic_missile") -> dict:
    return {
        "id": spell_id,
        "name": "Magic Missile",
        "level": 1,
        "tradition": "arcane",
        "range": "near",
        "target": "single",
        "duration": "instant",
        "save": {"stat": None, "effect": "none"},
        "effect_template": "Force dart, 1 momentum damage, auto-hit",
        "components": {"verbal": True, "somatic": True, "material": None},
        "backlash": None,
        "narrator_register": "A bolt of glowing force.",
        "hard_limits_check": [],
        "domain": "physical",
        "otel_attrs": ["cast_intent"],
    }


def test_spell_catalog_rejects_duplicate_ids():
    from pydantic import ValidationError

    from sidequest.magic.spell_catalog import SpellCatalog

    payload = {
        "version": "0.1.0",
        "genre": "caverns_and_claudes",
        "tradition": "arcane",
        "level": 1,
        "spells": [_spell_dict("magic_missile"), _spell_dict("magic_missile")],
    }
    with pytest.raises(ValidationError, match="magic_missile"):
        SpellCatalog.model_validate(payload)


def test_spell_save_effect_rejects_unknown_value():
    from pydantic import ValidationError

    from sidequest.magic.spell_catalog import SpellSave

    with pytest.raises(ValidationError, match="negate"):
        SpellSave(stat=None, effect="negate")  # typo of "negates"


def test_spell_save_effect_accepts_partial_form():
    from sidequest.magic.spell_catalog import SpellSave

    s = SpellSave(stat="DEX", effect="partial:half_damage")
    assert s.stat == "DEX"
    assert s.effect == "partial:half_damage"
