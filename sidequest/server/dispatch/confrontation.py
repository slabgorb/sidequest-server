"""Confrontation-def lookup + CONFRONTATION payload assembly.

Port of sidequest-api/crates/sidequest-server/src/dispatch/response.rs
confrontation-def resolution and payload construction. Story 3.4.

Story 47-3 (Phase 5) extends this with magic-confrontation outcome
resolution: ``resolve_magic_confrontation`` looks up a magic
confrontation by id, applies its branch's mandatory_outputs, and
returns a CONFRONTATION_OUTCOME payload for the WebSocket dispatcher.
"""

from __future__ import annotations

from typing import Any, Literal

from sidequest.game.encounter import StructuredEncounter
from sidequest.game.session import GameSnapshot
from sidequest.genre.models.rules import ConfrontationDef
from sidequest.magic.outputs import apply_mandatory_outputs

_BranchName = Literal["clear_win", "pyrrhic_win", "clear_loss", "refused"]


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
        "player_metric": encounter.player_metric.model_dump(mode="json"),
        "opponent_metric": encounter.opponent_metric.model_dump(mode="json"),
        "beats": [b.model_dump(mode="json") for b in cdef.beats],
        "secondary_stats": (
            encounter.secondary_stats.model_dump(mode="json")
            if encounter.secondary_stats is not None
            else None
        ),
        "genre_slug": genre_slug,
        "mood": mood,
        "active": not encounter.resolved,
    }


def resolve_magic_confrontation(
    *,
    snapshot: GameSnapshot,
    confrontation_id: str,
    branch: _BranchName,
    actor: str,
) -> dict[str, Any] | None:
    """Resolve a magic confrontation outcome — Story 47-3.

    Looks up ``confrontation_id`` on ``snapshot.magic_state.confrontations``;
    if found, applies the branch's mandatory_outputs via
    ``apply_mandatory_outputs`` and returns a CONFRONTATION_OUTCOME
    payload dict matching the UI's ``ConfrontationOutcome`` shape:

        {
          "confrontation_id": str,
          "label": str,
          "branch": "clear_win" | "pyrrhic_win" | "clear_loss" | "refused",
          "mandatory_outputs": list[str],
        }

    Returns ``None`` when the confrontation is not in MagicState (not a
    magic confrontation, or magic_state not loaded). Caller decides
    whether to dispatch ``CONFRONTATION_OUTCOME`` over the WebSocket.

    No silent fallback (CLAUDE.md): a confrontation that exists but
    lacks the requested branch raises KeyError — that's a content bug
    in confrontations.yaml, not a runtime fallback decision.
    """
    if snapshot.magic_state is None:
        return None
    magic_conf = next(
        (c for c in snapshot.magic_state.confrontations if c.id == confrontation_id),
        None,
    )
    if magic_conf is None:
        return None
    branch_def = magic_conf.outcomes[branch]
    mandatory_outputs = list(branch_def.mandatory_outputs)
    apply_mandatory_outputs(
        snapshot=snapshot,
        outputs=mandatory_outputs,
        actor=actor,
    )
    return {
        "confrontation_id": confrontation_id,
        "label": magic_conf.label,
        "branch": branch,
        "mandatory_outputs": mandatory_outputs,
    }


def build_clear_confrontation_payload(
    *,
    encounter_type: str,
    genre_slug: str,
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
        "player_metric": {},
        "opponent_metric": {},
        "beats": [],
        "secondary_stats": None,
        "genre_slug": genre_slug,
        "mood": None,
        "active": False,
    }
