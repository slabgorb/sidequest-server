"""interior.* OTEL spans — chassis interior map.

Per CLAUDE.md OTEL Observability Principle: every backend fix that
touches a subsystem MUST add OTEL spans. The Ship-tab renderer fires
``interior.render`` per fetch; ``interior.position_change`` fires when
the narrator state-patches an actor's ``current_room``. The dashboard
uses these to confirm the map is actually re-rendering and that
positions are moving (vs. Claude winging it without state mutation).
"""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_INTERIOR_RENDER = "interior.render"
SPAN_INTERIOR_POSITION_CHANGE = "interior.position_change"

FLAT_ONLY_SPANS.update({SPAN_INTERIOR_RENDER, SPAN_INTERIOR_POSITION_CHANGE})


def emit_interior_render(
    *,
    chassis_instance_id: str,
    actor_count: int,
    tracked_pcs: int,
    tracked_npcs: int,
    output_size_bytes: int,
) -> None:
    """Emit an ``interior.render`` span. Fire-and-forget."""
    with Span.open(
        SPAN_INTERIOR_RENDER,
        attrs={
            "chassis_instance_id": chassis_instance_id,
            "actor_count": int(actor_count),
            "tracked_pcs": int(tracked_pcs),
            "tracked_npcs": int(tracked_npcs),
            "output_size_bytes": int(output_size_bytes),
        },
    ):
        pass


def emit_interior_position_change(
    *,
    actor_id: str,
    from_room: str | None,
    to_room: str,
    source: str,
) -> None:
    """Emit an ``interior.position_change`` span when an actor moves rooms."""
    with Span.open(
        SPAN_INTERIOR_POSITION_CHANGE,
        attrs={
            "actor_id": actor_id,
            "from_room": from_room or "",
            "to_room": to_room,
            "source": source,
        },
    ):
        pass
