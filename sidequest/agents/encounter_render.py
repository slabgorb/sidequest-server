"""Render a StructuredEncounter into the Valley-zone summary string.

Consumed by session_handler._build_turn_context to populate
TurnContext.encounter_summary. The narrator uses this + the
confrontation_def beat listing (see narrator.build_encounter_context)
to emit well-formed beat_selections. Story 3.4.

TODO Task 11: rewrite render_encounter_summary for dual ascending dials.
_DIRECTION_LABELS and the single-dial metric rendering have been removed
as part of the MetricDirection → dual-dial migration. Full rewrite pending.
"""
from __future__ import annotations

from sidequest.game.encounter import StructuredEncounter


def render_encounter_summary(enc: StructuredEncounter) -> str:
    """Render an encounter's live state for the narrator's Valley zone.

    TODO Task 11: rewrite for dual ascending dials (player_metric + opponent_metric).
    """
    raise NotImplementedError(
        "render_encounter_summary: rewrite pending in Task 11 "
        "(dual-dial migration — MetricDirection removed)"
    )
