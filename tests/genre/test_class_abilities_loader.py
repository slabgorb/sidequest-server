"""Story 2026-05-10 — class mechanical surface.

Loader-level checks for the new `abilities` key on ClassDef and the
`taunt` beat for Fighter.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.genre.loader import load_genre_pack


GENRE_ROOT = Path(__file__).parents[2] / "../sidequest-content/genre_packs"


def test_caverns_and_claudes_loads_with_taunt_beat():
    pack = load_genre_pack(GENRE_ROOT.resolve() / "caverns_and_claudes")
    fighter = next(c for c in pack.classes if c.id == "fighter")
    assert "taunt" in fighter.encounter_beat_choices, (
        "Fighter must declare 'taunt' in encounter_beat_choices"
    )
    all_beat_ids = {b.id for cd in pack.rules.confrontations for b in cd.beats}
    assert "taunt" in all_beat_ids, "rules.yaml must declare a 'taunt' beat"
