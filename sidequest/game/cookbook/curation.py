"""world_register hard filter (spec §5).

Allowlist gate + deny rules, run BEFORE any RACE roll. marquee rows are
exempt from denial and survive unconditionally (Diamonds-and-Coal,
ADR-014). No silent substitution — a denied row is simply absent.
"""

from __future__ import annotations

from sidequest.game.cookbook.corpus import name_matches
from sidequest.game.cookbook.models import CorpusMonster, WorldRegister


def _denied(mon: CorpusMonster, reg: WorldRegister) -> bool:
    if mon.type in reg.deny.types:
        return True
    if set(reg.deny.tags) & set(mon.tags):
        return True
    return any(name_matches(mon.name, g) for g in reg.deny.name_glob)


def apply_world_register(corpus: list[CorpusMonster], reg: WorldRegister) -> list[CorpusMonster]:
    """Return the curated roll-space. marquee survives denial + allowlist."""
    marquee = set(reg.marquee)
    kept: list[CorpusMonster] = []
    for mon in corpus:
        if mon.name in marquee:
            kept.append(mon)
            continue
        if mon.type not in reg.allow_types:
            continue
        if _denied(mon, reg):
            continue
        kept.append(mon)
    return kept
