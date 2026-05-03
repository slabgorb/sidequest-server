"""Tests for compute_courses + PlottedCourse model."""
from __future__ import annotations

import pytest

from sidequest.orbital.course import (
    CourseRow,
    CourseSource,
    PlottedCourse,
)


def test_plotted_course_construction() -> None:
    pc = PlottedCourse(
        to_body_id="tethys_watch",
        label="Tethys Watch",
        eta_hours=12.0,
        delta_v=0.4,
        plotted_at_t_hours=120.0,
        source=CourseSource.IN_SCOPE,
    )
    assert pc.to_body_id == "tethys_watch"
    assert pc.label == "Tethys Watch"


def test_plotted_course_rejects_extra_fields() -> None:
    with pytest.raises(Exception):
        PlottedCourse(
            to_body_id="x",
            eta_hours=0.0,
            delta_v=0.0,
            plotted_at_t_hours=0.0,
            source=CourseSource.IN_SCOPE,
            extra_field="boom",  # type: ignore[call-arg]
        )


def test_course_row_carries_label_hint_for_quest_objective() -> None:
    row = CourseRow(
        to_body_id="deep_root",
        eta_hours=30.0,
        delta_v=1.0,
        source=CourseSource.QUEST_OBJECTIVE,
        label_hint="Hessler's manifest",
    )
    assert row.label_hint == "Hessler's manifest"


def test_course_source_priority_ordering() -> None:
    # Quest > recent_mention > in_scope, used by the 12-cap selector.
    assert (
        CourseSource.QUEST_OBJECTIVE.priority
        > CourseSource.RECENT_MENTION.priority
        > CourseSource.IN_SCOPE.priority
    )


def test_game_snapshot_has_plotted_course_field_default_none() -> None:
    from sidequest.game.session import GameSnapshot

    snap = GameSnapshot()
    assert snap.plotted_course is None


def test_game_snapshot_quest_anchors_default_empty() -> None:
    from sidequest.game.session import GameSnapshot

    snap = GameSnapshot()
    assert snap.quest_anchors == []


def test_game_snapshot_round_trip_with_plotted_course() -> None:
    from sidequest.game.session import GameSnapshot

    snap = GameSnapshot(
        plotted_course=PlottedCourse(
            to_body_id="deep_root",
            label="Deep Root",
            eta_hours=30.0,
            delta_v=1.0,
            plotted_at_t_hours=42.0,
            source=CourseSource.QUEST_OBJECTIVE,
        ),
        quest_anchors=["deep_root", "the_gate"],
    )
    payload = snap.model_dump()
    restored = GameSnapshot.model_validate(payload)
    assert restored.plotted_course is not None
    assert restored.plotted_course.to_body_id == "deep_root"
    assert restored.quest_anchors == ["deep_root", "the_gate"]
