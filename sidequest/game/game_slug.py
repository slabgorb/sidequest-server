"""Game slug generation and parsing.

A game slug is the canonical identifier for a game:
    <YYYY-MM-DD>-<world-slug>

Same-day + same-world collisions are the resume path — they are expected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

SLUG_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-([a-z0-9][a-z0-9-]*)$")


class InvalidSlugError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedSlug:
    date: date
    world_slug: str


def generate_slug(world_slug: str, today: date) -> str:
    if not world_slug:
        raise ValueError("world_slug must not be empty")
    return f"{today.isoformat()}-{world_slug}"


def parse_slug(slug: str) -> ParsedSlug:
    m = SLUG_RE.match(slug)
    if not m:
        raise InvalidSlugError(f"not a valid game slug: {slug!r}")
    y, mo, d, world = m.groups()
    try:
        parsed_date = date(int(y), int(mo), int(d))
    except ValueError as exc:
        raise InvalidSlugError(f"invalid date in slug {slug!r}: {exc}") from exc
    return ParsedSlug(date=parsed_date, world_slug=world)
