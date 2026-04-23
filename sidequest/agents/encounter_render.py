"""Render a StructuredEncounter into the Valley-zone summary string.

Consumed by session_handler._build_turn_context to populate
TurnContext.encounter_summary. The narrator uses this + the
confrontation_def beat listing (see narrator.build_encounter_context)
to emit well-formed beat_selections. Story 3.4.
"""
from __future__ import annotations

from sidequest.game.encounter import MetricDirection, StructuredEncounter


_DIRECTION_LABELS: dict[MetricDirection, str] = {
    MetricDirection.Ascending: "ascending",
    MetricDirection.Descending: "descending",
    MetricDirection.Bidirectional: "bidirectional",
}


def render_encounter_summary(enc: StructuredEncounter) -> str:
    """Render an encounter's live state for the narrator's Valley zone."""
    lines: list[str] = [f"encounter_type: {enc.encounter_type}", f"beat: {enc.beat}"]
    if enc.structured_phase is not None:
        lines.append(f"phase: {enc.structured_phase.value}")
    m = enc.metric
    direction_label = _DIRECTION_LABELS[m.direction]
    bounds: list[str] = []
    if m.threshold_low is not None:
        bounds.append(f"low={m.threshold_low}")
    if m.threshold_high is not None:
        bounds.append(f"high={m.threshold_high}")
    bounds_part = (", " + ", ".join(bounds)) if bounds else ""
    lines.append(
        f"metric: {m.name} {m.current}/{m.starting} "
        f"({direction_label}{bounds_part})"
    )
    if enc.actors:
        lines.append("actors:")
        for a in enc.actors:
            lines.append(f"- {a.name} ({a.role})")
    if enc.mood_override:
        lines.append(f"mood: {enc.mood_override}")
    return "\n".join(lines)
