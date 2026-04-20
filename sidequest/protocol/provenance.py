"""Provenance types for the four-tier content resolver.

Port of sidequest-protocol/src/provenance.rs.

These types travel on the wire as part of GameMessage payloads so the
GM panel can surface where a given resolved value came from
(Global / Genre / World / Culture) and the full merge trail that
produced it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Tier(str, Enum):
    """Content-inheritance tier. Always walked in this order: Global, Genre, World, Culture."""

    global_ = "global"
    genre = "genre"
    world = "world"
    culture = "culture"


class Span(BaseModel):
    """Line range in a YAML source file (1-based lines, 0-based cols)."""

    start_line: int
    """First line of the range (1-based)."""
    start_col: int
    """First column of the range (0-based)."""
    end_line: int
    """Last line of the range (1-based, inclusive)."""
    end_col: int
    """Last column of the range (0-based, exclusive)."""


class ContributionKind(str, Enum):
    """How a later tier's value relates to the value introduced by an earlier tier."""

    initial = "initial"
    """This tier introduced the value for the first time."""
    replaced = "replaced"
    """This tier replaced the value wholesale."""
    appended = "appended"
    """This tier appended entries to a list value."""
    merged = "merged"
    """This tier deep-merged a map value."""


class MergeStep(BaseModel):
    """One step in the merge trail — records which tier and file contributed."""

    tier: Tier
    """The tier that made this contribution."""
    file: str
    """Source file path relative to the genre pack root."""
    span: Span | None = None
    """Location within the file, if available."""
    contribution: ContributionKind
    """How this tier's value related to the previous value."""


class Provenance(BaseModel):
    """Full provenance for a resolved content value."""

    source_tier: Tier
    """The tier that produced the final resolved value."""
    source_file: str
    """Source file path relative to the genre pack root."""
    source_span: Span | None = None
    """Location within the source file, if available."""
    merge_trail: list[MergeStep]
    """Ordered list of tier contributions that produced the final value."""
