"""Story 2026-05-10 — class mechanical surface.

Loader-level checks for the new `abilities` key on ClassDef and the
`taunt` beat for Fighter.
"""
from __future__ import annotations

from pathlib import Path

from sidequest.genre.loader import load_genre_pack

GENRE_ROOT = Path(__file__).parents[2] / "../sidequest-content/genre_packs"


def test_caverns_and_claudes_loads_with_taunt_beat():
    pack = load_genre_pack(GENRE_ROOT.resolve() / "caverns_and_claudes")
    fighter = next(c for c in pack.classes if c.id == "fighter")
    assert "taunt" in fighter.encounter_beat_choices, (
        "Fighter must declare 'taunt' in encounter_beat_choices"
    )
    all_beat_ids = {b.id for cd in pack.rules.confrontations for b in cd.beats}
    assert "taunt" in all_beat_ids, "rules.yaml must declare a 'taunt' beat"


def test_class_def_parses_abilities_key():
    """A class with abilities: yields a list of ClassAbilityDef entries."""
    from sidequest.genre.models.character import ClassAbilityDef, ClassDef

    cd = ClassDef.model_validate(
        {
            "id": "cleric",
            "display_name": "Cleric",
            "rpg_role": "healer",
            "jungian_default": "caregiver",
            "prime_requisite": "WIS",
            "minimum_score": 9,
            "kit_table": "cleric_kit",
            "encounter_beat_choices": ["attack", "defend", "flee", "turn_undead"],
            "abilities": [
                {
                    "name": "Turn Undead",
                    "genre_description": "He raises the symbol; the unliving recoil.",
                    "mechanical_effect": "2d6 vs HD; loud; fails on intelligent unliving.",
                    "involuntary": False,
                }
            ],
        }
    )
    assert len(cd.abilities) == 1
    assert isinstance(cd.abilities[0], ClassAbilityDef)
    assert cd.abilities[0].name == "Turn Undead"
    assert cd.abilities[0].involuntary is False


def test_class_def_default_empty_abilities():
    """Absent abilities: → empty list. Mage path."""
    from sidequest.genre.models.character import ClassDef

    cd = ClassDef.model_validate(
        {
            "id": "mage",
            "display_name": "Mage",
            "rpg_role": "control",
            "jungian_default": "magician",
            "prime_requisite": "INT",
            "minimum_score": 9,
            "kit_table": "mage_kit",
            "encounter_beat_choices": ["attack", "defend", "flee", "cast_spell"],
        }
    )
    assert cd.abilities == []
