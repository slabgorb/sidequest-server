"""Corpus matching primitives — glob + clause/predicate evaluation.

Shared by curation (world_register) and RACE filter resolution so the
match semantics are defined exactly once.
"""

from __future__ import annotations

import fnmatch

from sidequest.game.cookbook.models import CorpusMonster, FilterClause


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
