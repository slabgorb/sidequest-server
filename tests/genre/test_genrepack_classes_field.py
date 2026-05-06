"""Tests for GenrePack.classes field (Task 2 — C&C classic-classes)."""

from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.pack import GenrePack


def test_genrepack_has_classes_field_default_empty():
    """GenrePack.classes defaults to empty list when not supplied."""
    # model_construct bypasses validation for required fields we're not testing.
    pack = GenrePack.model_construct()
    assert pack.classes == []


def test_genrepack_accepts_classes_list():
    """GenrePack.classes accepts a list of ClassDef entries."""
    fighter = ClassDef(
        id="fighter",
        display_name="Fighter",
        rpg_role="tank",
        jungian_default="hero",
        prime_requisite="STR",
        minimum_score=9,
        kit_table="fighter_kit",
    )
    pack = GenrePack.model_construct(classes=[fighter])
    assert pack.classes[0].id == "fighter"
