"""Render a StructuredEncounter into the Valley-zone summary string.

Consumed by session_handler._build_turn_context to populate
TurnContext.encounter_summary. The narrator uses this + the
confrontation_def beat listing (see narrator.build_encounter_context)
to emit well-formed beat_selections. Story 3.4.

Task 12 (2026-04-25): Implemented for dual ascending dials
(player_metric + opponent_metric) per the dual-track momentum spec.
"""
from __future__ import annotations

from sidequest.game.encounter import StructuredEncounter


def render_encounter_summary(enc: StructuredEncounter) -> str:
    """Render an encounter's live state for the narrator's Valley zone.

    Dual-dial format: both player and opponent ascending dials shown
    with their current/threshold values and the active phase.
    """
    phase = enc.structured_phase.value if enc.structured_phase else "Setup"
    pm = enc.player_metric
    om = enc.opponent_metric

    lines = [
        f"[ENCOUNTER: {enc.encounter_type}]",
        f"Phase: {phase}  Beat: {enc.beat}",
        f"Player {pm.name}: {pm.current}/{pm.threshold}",
        f"Opponent {om.name}: {om.current}/{om.threshold}",
    ]

    if enc.tags:
        tag_strs = [
            f"{t.text}({'fleeting' if t.fleeting else 'persistent'}, leverage={t.leverage})"
            for t in enc.tags
        ]
        lines.append(f"Tags: {', '.join(tag_strs)}")

    if enc.mood_override:
        lines.append(f"Mood: {enc.mood_override}")

    if enc.narrator_hints:
        lines.append(f"Hints: {'; '.join(enc.narrator_hints)}")

    return "\n".join(lines)
