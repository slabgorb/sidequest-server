"""Curation hard filter — spec §5/§9: denied rows gone, marquee survives."""

from __future__ import annotations

from sidequest.game.cookbook.curation import apply_world_register
from sidequest.game.cookbook.models import CorpusMonster, WorldRegister


def _mon(name: str, typ: str, tags=None, cr: float = 1.0) -> CorpusMonster:
    return CorpusMonster(
        name=name,
        size="Medium",
        type=typ,
        tags=tags or [],
        alignment="NE",
        cr=cr,
        xp=200,
        source="t",
    )


REGISTER = WorldRegister(
    register="grave",
    allow_types=["Undead", "Aberration", "Construct"],
    deny={"types": ["Celestial", "Fey"], "tags": ["titan"], "name_glob": ["*pixie*"]},
    marquee=["Lich"],
)


def test_denied_type_removed() -> None:
    corpus = [_mon("Solar", "Celestial"), _mon("Skeleton", "Undead")]
    kept = apply_world_register(corpus, REGISTER)
    assert [m.name for m in kept] == ["Skeleton"]


def test_type_not_in_allowlist_removed() -> None:
    # Dragon is neither allowed nor explicitly denied → removed (allowlist gate).
    corpus = [_mon("Adult Red Dragon", "Dragon"), _mon("Skeleton", "Undead")]
    kept = apply_world_register(corpus, REGISTER)
    assert [m.name for m in kept] == ["Skeleton"]


def test_denied_tag_removed() -> None:
    corpus = [_mon("Empyrean", "Giant", tags=["titan"])]
    assert apply_world_register(corpus, REGISTER) == []


def test_denied_name_glob_removed() -> None:
    corpus = [_mon("Pixie", "Fey")]
    assert apply_world_register(corpus, REGISTER) == []


def test_marquee_exempt_from_denial() -> None:
    # Lich is Undead (allowed) but also marquee — survives even if a deny
    # rule would catch it. Construct a deny that would hit it by name.
    reg = WorldRegister(
        register="g",
        allow_types=["Undead"],
        deny={"name_glob": ["*lich*"]},
        marquee=["Lich"],
    )
    corpus = [_mon("Lich", "Undead")]
    kept = apply_world_register(corpus, reg)
    assert [m.name for m in kept] == ["Lich"]
