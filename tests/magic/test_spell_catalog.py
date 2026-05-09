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


# ---------------------------------------------------------------------------
# Story 47-10 — Null-stat auto-apply rule (AC5)
# ---------------------------------------------------------------------------
# Codified 2026-05-09: save.stat: null means the spell auto-applies (no
# opposed check, no save). The author cannot pair null-stat with
# save.effect != "none" — there is nothing for the defender to halve or
# negate when there is no save. This is an authoring-time validator
# error, not a runtime branch.
#
# Canonical null-stat spells in the v1 catalogs:
#   arcane: magic_missile, light, floating_disc, read_magic, hold_portal,
#           detect_magic, read_languages, shield, ventriloquism, ...
#   divine: cure_light_wounds (and reverse), light, detect_evil,
#           detect_magic, protection_from_evil, ...


def test_spell_save_null_stat_with_none_effect_is_valid():
    """The canonical auto-apply shape: stat=None, effect=none."""
    from sidequest.magic.spell_catalog import SpellSave

    s = SpellSave(stat=None, effect="none")
    assert s.stat is None
    assert s.effect == "none"


def test_spell_save_null_stat_paired_with_negates_rejected():
    """A spell with no save can't be 'negated' by the defender —
    contradictory authoring."""
    from pydantic import ValidationError

    from sidequest.magic.spell_catalog import SpellSave

    with pytest.raises(ValidationError, match=r"(stat|null|none)"):
        SpellSave(stat=None, effect="negates")


def test_spell_save_null_stat_paired_with_halves_rejected():
    from pydantic import ValidationError

    from sidequest.magic.spell_catalog import SpellSave

    with pytest.raises(ValidationError, match=r"(stat|null|none)"):
        SpellSave(stat=None, effect="halves")


def test_spell_save_null_stat_paired_with_partial_rejected():
    from pydantic import ValidationError

    from sidequest.magic.spell_catalog import SpellSave

    with pytest.raises(ValidationError, match=r"(stat|null|none)"):
        SpellSave(stat=None, effect="partial:half_damage")


def test_shipped_arcane_l1_catalog_passes_null_stat_validator():
    """The merged arcane_l1.yaml content must remain valid under the
    new null-stat rule. If this fails after the validator lands, an
    authoring fix is required to the content side, not a validator
    rollback."""
    from sidequest.magic.spell_catalog import load_spell_catalog

    pack_root = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
    arcane_yaml = pack_root / "caverns_and_claudes" / "spells" / "arcane_l1.yaml"
    if not arcane_yaml.is_file():
        pytest.skip("arcane_l1.yaml not present in content tree")
    cat = load_spell_catalog(arcane_yaml)
    # Magic Missile is the canonical null-stat / none-effect row.
    mm = cat.get("magic_missile")
    assert mm.save.stat is None
    assert mm.save.effect == "none"


def test_shipped_divine_l1_catalog_passes_null_stat_validator():
    from sidequest.magic.spell_catalog import load_spell_catalog

    pack_root = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
    divine_yaml = pack_root / "caverns_and_claudes" / "spells" / "divine_l1.yaml"
    if not divine_yaml.is_file():
        pytest.skip("divine_l1.yaml not present in content tree")
    cat = load_spell_catalog(divine_yaml)
    # Cure Light Wounds is the canonical divine null-stat row.
    clw = cat.get("cure_light_wounds")
    assert clw.save.stat is None
    assert clw.save.effect == "none"


# ---------------------------------------------------------------------------
# B/X B26 saving throws — category + requires_mind (Tasks 1-8 of saves plan)
# ---------------------------------------------------------------------------


def test_spell_save_category_defaults_rods_staves_spells():
    from sidequest.genre.models.rules import SaveCategory
    from sidequest.magic.spell_catalog import SpellSave

    s = SpellSave(stat="WIS", effect="negates")
    assert s.category is SaveCategory.rods_staves_spells


def test_spell_save_category_dragon_breath_requires_null_stat():
    from pydantic import ValidationError

    from sidequest.genre.models.rules import SaveCategory
    from sidequest.magic.spell_catalog import SpellSave

    with pytest.raises(ValidationError, match="dragon_breath"):
        SpellSave(stat="WIS", effect="halves", category=SaveCategory.dragon_breath)


def test_spell_save_category_dragon_breath_with_null_stat_ok():
    from sidequest.genre.models.rules import SaveCategory
    from sidequest.magic.spell_catalog import SpellSave

    s = SpellSave(stat=None, effect="halves", category=SaveCategory.dragon_breath)
    assert s.category is SaveCategory.dragon_breath


def test_spell_requires_mind_default_false():
    from sidequest.magic.spell_catalog import Spell, SpellComponents, SpellSave

    s = Spell(
        id="magic_missile",
        name="Magic Missile",
        level=1,
        tradition="arcane",
        range="near",
        target="single",
        duration="instant",
        save=SpellSave(stat=None, effect="none"),
        effect_template="auto-hit",
        components=SpellComponents(),
        backlash=None,
        narrator_register="A bolt.",
        domain="physical",
    )
    assert s.requires_mind is False
