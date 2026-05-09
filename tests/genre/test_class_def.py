"""Tests for ClassDef magic_config sub-model (Task 2.2)."""


def test_class_def_accepts_magic_config():
    from sidequest.genre.models.character import ClassDef

    c = ClassDef(
        id="mage",
        display_name="Mage",
        rpg_role="control",
        jungian_default="magician",
        prime_requisite="INT",
        minimum_score=9,
        kit_table="mage_kit",
        magic_access="learned_v1",
        magic_config={
            "tradition": "arcane",
            "slots_by_class_level": {"1": {"1": 1}},
            "starting_known_spells": 2,
            "save_dc_stat": "INT",
        },
    )
    assert c.magic_config is not None
    assert c.magic_config.tradition == "arcane"
    assert c.magic_config.slots_by_class_level["1"]["1"] == 1


def test_class_def_magic_config_optional_for_non_caster():
    from sidequest.genre.models.character import ClassDef

    c = ClassDef(
        id="fighter",
        display_name="Fighter",
        rpg_role="tank",
        jungian_default="hero",
        prime_requisite="STR",
        minimum_score=9,
        kit_table="fighter_kit",
    )
    assert c.magic_access is None
    assert c.magic_config is None
