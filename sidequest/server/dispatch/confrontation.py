"""Confrontation-def lookup + CONFRONTATION payload assembly.

Port of sidequest-api/crates/sidequest-server/src/dispatch/response.rs
confrontation-def resolution and payload construction. Story 3.4.
"""
from __future__ import annotations

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
