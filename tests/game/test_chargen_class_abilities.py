"""Story 2026-05-10 — _seed_class_abilities populates Class-source abilities."""
from __future__ import annotations

from sidequest.game.ability import AbilitySource
from sidequest.game.builder import _seed_class_abilities
from sidequest.game.character import AbilityDefinition


def _make_class_def(class_id: str, ability_name: str | None):
    from sidequest.genre.models.character import ClassAbilityDef, ClassDef

    abilities = []
    if ability_name:
        abilities = [
            ClassAbilityDef(
                name=ability_name,
                genre_description=f"{ability_name} prose.",
                mechanical_effect=f"{ability_name} effect.",
                involuntary=False,
            )
        ]
    return ClassDef(
        id=class_id,
        display_name=class_id.capitalize(),
        rpg_role="x",
        jungian_default="x",
        prime_requisite="STR",
        minimum_score=9,
        kit_table=f"{class_id}_kit",
        abilities=abilities,
    )


def test_seed_class_abilities_appends_one_with_class_source():
    abilities: list[AbilityDefinition] = []
    cd = _make_class_def("cleric", "Turn Undead")

    _seed_class_abilities(abilities, cd)

    assert len(abilities) == 1
    a = abilities[0]
    assert a.name == "Turn Undead"
    assert a.source == AbilitySource.Class
    assert a.genre_description == "Turn Undead prose."
    assert a.mechanical_effect == "Turn Undead effect."
    assert a.involuntary is False


def test_seed_class_abilities_noop_for_mage():
    abilities: list[AbilityDefinition] = []
    cd = _make_class_def("mage", None)

    _seed_class_abilities(abilities, cd)

    assert abilities == []


def test_seed_class_abilities_preserves_prior_entries():
    """The seam appends; it must not clobber scene-driven hints already in the list."""
    prior = AbilityDefinition(
        name="Prior",
        genre_description="x",
        mechanical_effect="y",
        involuntary=False,
        source=AbilitySource.Class,
    )
    abilities = [prior]
    cd = _make_class_def("cleric", "Turn Undead")

    _seed_class_abilities(abilities, cd)

    assert len(abilities) == 2
    assert abilities[0] is prior
    assert abilities[1].name == "Turn Undead"


def test_seed_item_abilities_is_callable_and_returns_none():
    """Contract test — locks the documented stub against accidental deletion.
    Spec 2026-05-10 §6.2."""
    from sidequest.game.builder import _seed_item_abilities

    abilities: list[AbilityDefinition] = []
    # `kit_def=None` is the current-story shape — the next story replaces this with
    # KitDefinition. The contract that matters here: callable, returns None,
    # leaves abilities untouched.
    result = _seed_item_abilities(abilities, kit_def=None)

    assert result is None
    assert abilities == []
