"""Load a beneath_sunden world dir into a typed CookbookBundle.

This is the COOKBOOK loader (oq-2). It is NOT oq-1's region_graph/themes
loader (spec §2) — distinct concern, distinct file. No silent fallback:
a missing required file raises FileNotFoundError naming the path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sidequest.game.cookbook.models import (
    Affinities,
    CorpusItem,
    CorpusMonster,
    LookDef,
    RaceDef,
    SpecialRoom,
    WorldRegister,
)


@dataclass(frozen=True)
class CookbookBundle:
    monsters: list[CorpusMonster]
    items: list[CorpusItem]
    register: WorldRegister
    races: list[RaceDef]
    looks: list[LookDef]
    affinities: Affinities
    specials: list[SpecialRoom]


def _load_yaml(path: Path) -> object:
    if not path.exists():
        raise FileNotFoundError(f"cookbook: required file missing: {path}")
    return yaml.safe_load(path.read_text())


def load_cookbook(world: Path) -> CookbookBundle:
    cp = world / "corpus"
    cb = world / "cookbook"
    monsters = [CorpusMonster(**r) for r in _load_yaml(cp / "monsters.yaml")]
    items = [CorpusItem(**r) for r in _load_yaml(cp / "items.yaml")]
    register = WorldRegister(**_load_yaml(world / "world_register.yaml"))
    race_dir = cb / "races"
    if not race_dir.is_dir():
        raise FileNotFoundError(f"cookbook: races dir missing: {race_dir}")
    races = [RaceDef(**_load_yaml(p)) for p in sorted(race_dir.glob("*.yaml"))]
    looks = [LookDef(**look) for look in _load_yaml(cb / "looks.yaml")["looks"]]
    affinities = Affinities(**_load_yaml(cb / "affinities.yaml"))
    specials = [SpecialRoom(**s) for s in _load_yaml(cb / "special_rooms.yaml")["special_rooms"]]
    return CookbookBundle(
        monsters=monsters,
        items=items,
        register=register,
        races=races,
        looks=looks,
        affinities=affinities,
        specials=specials,
    )
