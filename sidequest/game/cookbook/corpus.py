"""Corpus matching primitives — glob + clause/predicate evaluation.

Shared by curation (world_register) and RACE filter resolution so the
match semantics are defined exactly once.
"""

from __future__ import annotations

import fnmatch

from sidequest.game.cookbook.models import CorpusMonster, FilterClause, RaceDef


def name_matches(name: str, glob: str) -> bool:
    """Case-insensitive fnmatch (SRD names are Title Case; globs lower)."""
    return fnmatch.fnmatch(name.lower(), glob.lower())


def clause_matches(mon: CorpusMonster, clause: FilterClause) -> bool:
    """All present clause fields must hold (AND within a clause)."""
    if clause.type is not None and mon.type != clause.type:
        return False
    if clause.tags_any is not None and not set(clause.tags_any) & set(mon.tags):
        return False
    return clause.name_glob is None or name_matches(mon.name, clause.name_glob)


def any_of_matches(mon: CorpusMonster, clauses: list[FilterClause]) -> bool:
    """OR across clauses (spec §4.2 RACE filter.any_of)."""
    return any(clause_matches(mon, c) for c in clauses)


def resolve_race(
    corpus: list[CorpusMonster],
    race: RaceDef,
    *,
    cr_min: float | None = None,
    cr_max: float | None = None,
) -> list[CorpusMonster]:
    """RACE roll-space: corpus ∩ race.filter − race.deny, optional CR slice.

    Curation (world_register) is applied UPSTREAM of this — callers pass
    an already-curated corpus (spec §5: register runs before any RACE
    roll).
    """
    deny_types = set(race.deny.types)
    deny_tags = set(race.deny.tags)
    out: list[CorpusMonster] = []
    for mon in corpus:
        if not any_of_matches(mon, race.filter.any_of):
            continue
        if mon.type in deny_types or (deny_tags & set(mon.tags)):
            continue
        if any(name_matches(mon.name, g) for g in race.deny.name_glob):
            continue
        if cr_min is not None and mon.cr < cr_min:
            continue
        if cr_max is not None and mon.cr > cr_max:
            continue
        out.append(mon)
    return out
