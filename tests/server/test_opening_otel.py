"""Tests that opening pipeline emits OTEL spans on the right transitions."""

from __future__ import annotations

from unittest.mock import patch

from sidequest.genre.models.narrative import (
    Opening,
    OpeningSetting,
    OpeningTrigger,
)
from sidequest.server.dispatch.opening import (
    build_directive,
    record_opening_played,
)


def _opening_chassis() -> Opening:
    return Opening(
        id="test_op",
        triggers=OpeningTrigger(mode="solo"),
        setting=OpeningSetting(chassis_instance="kestrel", interior_room="galley"),
        establishing_narration="The galley is warm.",
        first_turn_invitation="Outside the porthole, void.",
    )


def _opening_location() -> Opening:
    return Opening(
        id="test_loc_op",
        triggers=OpeningTrigger(mode="solo"),
        setting=OpeningSetting(location_label="the Promenade"),
        establishing_narration="The Promenade is full.",
        first_turn_invitation="A bell rings.",
    )


def test_build_directive_location_emits_span() -> None:
    """When chassis is None, dispatch picks the location renderer and emits the span."""
    with patch("sidequest.server.dispatch.opening.Span.open") as span_open:
        # Real Span.open is a contextmanager; the mock auto-enters/exits cleanly.
        build_directive(
            opening=_opening_location(),
            chassis=None,
            authored_crew=[],
            magic_register="",
            bond_tier_for_pc="trusted",
            per_pc_beat=None,
            pc_first_name="Z",
            pc_last_name="J",
            pc_nickname="",
            present_npcs=[],
        )
        names = [call.args[0] for call in span_open.call_args_list]
        assert "opening.directive_rendered" in names


def test_record_opening_played_emits_span() -> None:
    with patch("sidequest.server.dispatch.opening.Span.open") as span_open:
        record_opening_played(
            opening_id="test_op",
            turn_id=1,
        )
        names = [call.args[0] for call in span_open.call_args_list]
        assert "opening.played" in names
