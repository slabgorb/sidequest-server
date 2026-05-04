"""Wiring tests: course_intent sidecar variants land as state patches.

Sidecar JSON appears inside the narrator's game_patch fenced block.
The narration_apply pipeline parses the JSON, dispatches typed
intents to handlers/course_intent.py, which validates against the
current turn's compute_courses output and emits state patches.
"""
from __future__ import annotations

import pytest

from sidequest.protocol.course_intent import (
    CancelCourseSidecar,
    PlotCourseSidecar,
    parse_course_sidecar,
)


def test_plot_course_sidecar_round_trip() -> None:
    payload = {"intent": "plot_course", "course_id": "tethys_watch"}
    sc = PlotCourseSidecar.model_validate(payload)
    assert sc.course_id == "tethys_watch"


def test_cancel_course_sidecar_round_trip() -> None:
    payload = {"intent": "cancel_course"}
    sc = CancelCourseSidecar.model_validate(payload)
    assert sc.intent == "cancel_course"


def test_parse_course_sidecar_returns_typed_variant() -> None:
    plot = parse_course_sidecar({"intent": "plot_course", "course_id": "x"})
    assert isinstance(plot, PlotCourseSidecar)
    cancel = parse_course_sidecar({"intent": "cancel_course"})
    assert isinstance(cancel, CancelCourseSidecar)


def test_parse_course_sidecar_returns_none_for_unrelated_payloads() -> None:
    """Sidecar parser is tolerant: non-course intents yield None so the
    pipeline can ignore them and let other handlers process the same
    game_patch."""
    assert parse_course_sidecar({"intent": "roll_dice"}) is None
    assert parse_course_sidecar({}) is None
    assert parse_course_sidecar({"intent": "plot_course"}) is None  # missing course_id


def test_plot_course_sidecar_forbids_extra_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlotCourseSidecar.model_validate(
            {"intent": "plot_course", "course_id": "x", "secret": "boom"}
        )


def test_handle_plot_course_sets_snapshot_field() -> None:
    """Accept path: course_id is in compute_courses output → state
    patch on /plotted_course → snapshot.plotted_course populated."""
    from sidequest.game.session import GameSnapshot
    from sidequest.handlers.course_intent import handle_course_sidecar
    from sidequest.orbital.course import (
        CourseRow,
        CourseSource,
    )

    snap = GameSnapshot(party_body_id="near", clock_t_hours=42.0)
    available = {
        "mid": CourseRow(
            to_body_id="mid",
            eta_hours=30.0,
            delta_v=1.0,
            source=CourseSource.IN_SCOPE,
        ),
    }
    sc = PlotCourseSidecar(intent="plot_course", course_id="mid")

    result = handle_course_sidecar(
        sidecar=sc,
        snapshot=snap,
        available_courses=available,
    )
    assert result.accepted is True
    assert snap.plotted_course is not None
    assert snap.plotted_course.to_body_id == "mid"
    assert snap.plotted_course.eta_hours == 30.0
    assert snap.plotted_course.delta_v == 1.0
    assert snap.plotted_course.plotted_at_t_hours == 42.0
    assert snap.plotted_course.source == CourseSource.IN_SCOPE


def test_handle_plot_course_rejects_unknown_id() -> None:
    """Reject path: course_id not in available_courses → snapshot
    unchanged, result.accepted=False, result.reason set."""
    from sidequest.game.session import GameSnapshot
    from sidequest.handlers.course_intent import handle_course_sidecar

    snap = GameSnapshot(party_body_id="near")
    sc = PlotCourseSidecar(intent="plot_course", course_id="maltese_falcon")
    result = handle_course_sidecar(
        sidecar=sc,
        snapshot=snap,
        available_courses={},
    )
    assert result.accepted is False
    assert "not_in_scope" in result.reason or "unknown" in result.reason
    assert snap.plotted_course is None


def test_handle_cancel_course_clears_field() -> None:
    from sidequest.game.session import GameSnapshot
    from sidequest.handlers.course_intent import handle_course_sidecar
    from sidequest.orbital.course import CourseSource, PlottedCourse

    snap = GameSnapshot(
        party_body_id="near",
        plotted_course=PlottedCourse(
            to_body_id="mid",
            eta_hours=30.0,
            delta_v=1.0,
            plotted_at_t_hours=10.0,
            source=CourseSource.IN_SCOPE,
        ),
    )
    sc = CancelCourseSidecar()
    result = handle_course_sidecar(
        sidecar=sc,
        snapshot=snap,
        available_courses={},
    )
    assert result.accepted is True
    assert snap.plotted_course is None


def test_handle_cancel_course_when_no_plot_is_no_op() -> None:
    """Cancel intent when no course is plotted → accepted=True, no-op,
    flagged via was_already_clear."""
    from sidequest.game.session import GameSnapshot
    from sidequest.handlers.course_intent import handle_course_sidecar

    snap = GameSnapshot()
    sc = CancelCourseSidecar()
    result = handle_course_sidecar(
        sidecar=sc,
        snapshot=snap,
        available_courses={},
    )
    assert result.accepted is True
    assert result.was_already_clear is True
    assert snap.plotted_course is None


def test_course_span_helpers_emit_without_error() -> None:
    """Smoke wiring test: span functions can be called without raising.

    Real assertions on attribute values belong in OTEL test infrastructure
    (search for tests/telemetry/ for the project's pattern). This smoke
    is sufficient to catch missing imports / arg signature drift."""
    from sidequest.orbital.course import CourseSource, PlottedCourse
    from sidequest.telemetry.spans.course import (
        emit_course_cancel,
        emit_course_compute,
        emit_course_plot_accepted,
        emit_course_plot_rejected,
        emit_course_render_overlay,
    )

    pc = PlottedCourse(
        to_body_id="mid",
        eta_hours=30.0,
        delta_v=1.0,
        plotted_at_t_hours=42.0,
        source=CourseSource.IN_SCOPE,
    )
    emit_course_compute(course_count=4, in_scope=2, recent=1, quest=1, dropped_by_cap=0)
    emit_course_plot_accepted(from_body="near", course=pc)
    emit_course_plot_rejected(
        course_id="bogus", reason="not_in_scope", available_ids=["mid", "far"]
    )
    emit_course_cancel(was_already_clear=False)
    emit_course_render_overlay(to_body="mid", bezier_control_offset_au=0.4)
