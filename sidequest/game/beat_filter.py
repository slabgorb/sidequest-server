"""Per-class beat filter — single source of truth for 'what can the player do this turn?'.

Per spec docs/superpowers/specs/2026-05-08-cnc-bx-class-beats-morale-design.md §4.3.
This is the seam where future story #2 (B/X memorization) will plug in additional
named-spell gating for cast_spell — extend the filter, do not replace it.
"""

from __future__ import annotations

from sidequest.genre.error import PackError
from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.rules import BeatDef, ConfrontationDef


def beats_available_for(
    confrontation: ConfrontationDef,
    class_def: ClassDef,
    spell_slots_remaining: float,
) -> list[BeatDef]:
    """Return the BeatDefs the given class can select this turn.

    Filter chain:
      1. class_filter on each beat (None = universal; non-empty = whitelist)
      2. class_def.encounter_beat_choices intersection (per-class whitelist)
      3. resource gates (cast_spell requires spell_slots_remaining >= 1.0)
    """
    if not class_def.encounter_beat_choices:
        raise PackError(f"class {class_def.display_name!r} has empty encounter_beat_choices")

    pool: list[BeatDef] = []
    for beat in confrontation.beats:
        if beat.class_filter is not None and class_def.display_name not in beat.class_filter:
            continue
        if beat.id not in class_def.encounter_beat_choices:
            continue
        if beat.id == "cast_spell" and spell_slots_remaining < 1.0:
            continue
        pool.append(beat)
    return pool
