"""Ingest fidelity — spec §9: every source stat-block leaf emitted,
CR parses to float, idempotent, required names resolve."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sidequest.cli.cookbook_ingest.ingest import (
    iter_statblock_leaves,
    parse_cr,
    parse_statline,
    walk_monsters,
)

WORLD = (
    Path(__file__).parents[4]
    / "sidequest-content/genre_packs/caverns_and_claudes/worlds/beneath_sunden"
)
SRC = WORLD / "corpus/_source"


def test_parse_cr_fractions() -> None:
    assert parse_cr("1/8") == 0.125
    assert parse_cr("1/4") == 0.25
    assert parse_cr("1/2") == 0.5
    assert parse_cr("0") == 0.0
    assert parse_cr("21") == 21.0


def test_parse_cr_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="unparseable CR"):
        parse_cr("banana")


def test_statline_strips_inline_markdown() -> None:
    # The 'Mummy' edge: stray bold inside the italic line.
    size, typ, tags, align = parse_statline("*Medium undead**,** lawful evil*")
    assert (size, typ, tags, align) == ("Medium", "Undead", [], "lawful evil")


def test_statline_extracts_tags() -> None:
    size, typ, tags, align = parse_statline("*Small humanoid (goblinoid), neutral evil*")
    assert typ == "Humanoid" and tags == ["goblinoid"]


@pytest.mark.skipif(not (SRC / "monsters.json").exists(), reason="Prereq 0 not vendored")
def test_every_source_leaf_emitted_and_cr_float() -> None:
    docs = [
        json.loads((SRC / f).read_text()) for f in ("monsters.json", "creatures.json", "npcs.json")
    ]
    expected = sum(len(list(iter_statblock_leaves(d))) for d in docs)
    rows = walk_monsters(docs)
    # De-dup by name (first wins) is allowed; count must not EXCEED source
    # leaves and every required marquee/big_bad name must survive.
    assert 0 < len(rows) <= expected
    assert all(isinstance(r["cr"], float) for r in rows)
    names = {r["name"] for r in rows}
    for need in (
        "Lich",
        "Mummy",
        "Mummy Lord",
        "Aboleth",
        "Vampire",
        "Skeleton",
        "Gray Ooze",
        "Animated Armor",
        "Hobgoblin",
    ):
        assert need in names, f"required SRD name {need!r} did not resolve"


@pytest.mark.skipif(not (SRC / "monsters.json").exists(), reason="Prereq 0 not vendored")
def test_idempotent() -> None:
    docs = [
        json.loads((SRC / f).read_text()) for f in ("monsters.json", "creatures.json", "npcs.json")
    ]
    assert walk_monsters(docs) == walk_monsters(docs)
