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
