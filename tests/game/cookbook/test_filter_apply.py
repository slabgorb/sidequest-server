"""RACE filter resolution: any_of OR, per-RACE deny, CR-band slice."""

from __future__ import annotations

from sidequest.game.cookbook.corpus import resolve_race
from sidequest.game.cookbook.models import CorpusMonster, RaceDef


def _mon(name, typ, tags=None, cr=1.0) -> CorpusMonster:
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


UNDEAD = RaceDef(
    id="undead",
    display="The Restless",
    filter={"any_of": [{"type": "Undead"}, {"type": "Construct", "name_glob": "*animated*"}]},
    deny={"name_glob": ["*faerie*"]},
)


def test_any_of_or_semantics() -> None:
    corpus = [
        _mon("Skeleton", "Undead"),
        _mon("Animated Armor", "Construct"),
        _mon("Iron Golem", "Construct"),
        _mon("Goblin", "Humanoid"),
    ]
    got = {m.name for m in resolve_race(corpus, UNDEAD)}
    assert got == {"Skeleton", "Animated Armor"}


def test_per_race_deny_subtracts() -> None:
    corpus = [_mon("Skeleton", "Undead"), _mon("Faerie Wraith", "Undead")]
    got = {m.name for m in resolve_race(corpus, UNDEAD)}
    assert got == {"Skeleton"}


def test_cr_band_slice() -> None:
    corpus = [_mon("Skeleton", "Undead", cr=0.25), _mon("Lich", "Undead", cr=21)]
    got = {m.name for m in resolve_race(corpus, UNDEAD, cr_min=6, cr_max=30)}
    assert got == {"Lich"}
