"""Confrontation-def lookup + CONFRONTATION payload assembly.

Port of sidequest-api/crates/sidequest-server/src/dispatch/response.rs
confrontation-def resolution and payload construction. Story 3.4.
"""
from __future__ import annotations

from typing import Any

from sidequest.game.encounter import StructuredEncounter
from sidequest.genre.models.rules import ConfrontationDef


def find_confrontation_def(
    defs: list[ConfrontationDef],
    encounter_type: str,
) -> ConfrontationDef | None:
    """Return the ConfrontationDef whose ``confrontation_type`` equals ``encounter_type``.

    Exact string match — mirrors Rust's ``iter().find(|d| d.type == ty)``.
    Returns ``None`` when no def matches; callers MUST handle the miss
    (CLAUDE.md: no silent fallback — caller decides whether to error).
    """
    for d in defs:
        if d.confrontation_type == encounter_type:
            return d
    return None


def build_confrontation_payload(
    *,
    encounter: StructuredEncounter,
    cdef: ConfrontationDef,
    genre_slug: str,
) -> dict[str, Any]:
    """Assemble the CONFRONTATION payload the UI overlay consumes.

    Shape fixed by sidequest-ui/src/components/ConfrontationOverlay.tsx:42-58.
    Encounter mood_override beats the confrontation-def default mood.
    """
    if encounter.mood_override is not None:
        mood = encounter.mood_override
    elif cdef.mood is not None:
        mood = cdef.mood
    else:
        mood = ""
    return {
        "type": encounter.encounter_type,
        "label": cdef.label,
        "category": cdef.category,
        "actors": [a.model_dump(mode="json") for a in encounter.actors],
        "metric": encounter.metric.model_dump(mode="json"),
        "beats": [b.model_dump(mode="json") for b in cdef.beats],
        "secondary_stats": (
            encounter.secondary_stats.model_dump(mode="json")
            if encounter.secondary_stats is not None else None
        ),
        "genre_slug": genre_slug,
        "mood": mood,
        "active": not encounter.resolved,
    }


def build_clear_confrontation_payload(
    *, encounter_type: str, genre_slug: str,
) -> dict[str, Any]:
    """Minimal payload that tells the UI to unmount the overlay.

    App.tsx:435 — ``payload.active !== false`` is the dispatch branch; an
    explicit ``false`` is what clears the overlay. Other fields are
    required by the TS interface but ignored when active=false.
    """
    return {
        "type": encounter_type,
        "label": "",
        "category": "",
        "actors": [],
        "metric": {},
        "beats": [],
        "secondary_stats": None,
        "genre_slug": genre_slug,
        "mood": None,
        "active": False,
    }
