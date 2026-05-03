"""Render trigger policy contract — Story 45-30.

Single deterministic decision function that classifies why (or why not)
the visual-scene render dispatch should fire on a given turn. Inputs are
already-extracted structured signals on ``NarrationTurnResult`` plus an
out-of-band ``encounter_resolved_this_turn`` boolean derived in
``narration_apply``. No regex, no prose inference.

Background: Playtest 3 (Felix, 2026-04-19) produced ~6–8 renders out of
71 turns with no policy backing the selection — the dispatch gate was a
naked ``visual is None`` short-circuit. ADR-014 (Diamonds and Coal) and
the OTEL Observability Principle require an explicit, observable
contract: every render decision lands a watcher event the GM panel can
audit.

Priority order (first match wins):
    BEAT_FIRE > SCENE_CHANGE > NPC_INTRO > ENCOUNTER_RESOLVED > NONE_POLICY
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sidequest.agents.orchestrator import NarrationTurnResult


class RenderTriggerReason(StrEnum):
    """Why the render dispatch (didn't) fire on a given turn.

    Values are wire literals — they ship in the ``render.trigger``
    watcher event ``reason`` field and the GM panel filters on them.
    Renaming a value is a wire-breaking change.
    """

    BEAT_FIRE = "beat_fire"
    SCENE_CHANGE = "scene_change"
    NPC_INTRO = "npc_intro"
    ENCOUNTER_RESOLVED = "resolved"
    NONE_POLICY = "none_policy"


def classify_trigger(
    result: NarrationTurnResult,
    snapshot_location_before: str | None,
    encounter_resolved_this_turn: bool,
) -> RenderTriggerReason:
    """Return the highest-priority trigger reason for this turn.

    The function is pure and deterministic. ``visual_scene`` presence is
    deliberately NOT a signal — that was the pre-story behaviour and the
    reason banter turns rendered while named-NPC introductions did not.

    Args:
        result: The narrator's structured output for this turn.
        snapshot_location_before: ``snapshot.location`` BEFORE
            ``_apply_narration_result_to_snapshot`` mutates it. Pass
            ``None`` for brand-new games (entering the world counts as
            a scene change). Comparing against the post-apply location
            would always equal ``result.location`` and never register
            SCENE_CHANGE.
        encounter_resolved_this_turn: ``True`` when an active encounter
            transitioned to ``resolved`` on this turn. Threaded from
            the ``narration_apply`` seam — do not re-derive here.
    """
    # BEAT_FIRE: any trope/momentum beat resolved this turn.
    if result.beat_selections:
        return RenderTriggerReason.BEAT_FIRE

    # SCENE_CHANGE: the narrator's location differs from the pre-turn
    # snapshot location. Empty/None on either side counts as "no change"
    # except when the prior was None and the result names a place
    # (entering the world).
    result_location = (result.location or "").strip()
    before = (snapshot_location_before or "").strip()
    if result_location and result_location != before:
        return RenderTriggerReason.SCENE_CHANGE

    # NPC_INTRO: at least one mentioned NPC has ``is_new=True``.
    # Recurring NPCs (is_new=False) do not trigger — the story's
    # rationale is that the introduction is the diamond, not the
    # cigarette-sharing scene that follows.
    if any(getattr(npc, "is_new", False) for npc in (result.npcs_present or [])):
        return RenderTriggerReason.NPC_INTRO

    # ENCOUNTER_RESOLVED: the only signal whose source is the
    # narration_apply seam, not the orchestrator output.
    if encounter_resolved_this_turn:
        return RenderTriggerReason.ENCOUNTER_RESOLVED

    return RenderTriggerReason.NONE_POLICY
