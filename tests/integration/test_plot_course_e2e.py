"""End-to-end: narrator emits plot_course in game_patch → snapshot
mutates → next chart fetch returns SVG with overlay → cancel_course
clears.

Stops short of touching the WebSocket transport — that's covered
by tests/integration/test_orbital_e2e.py for the chart path; we
just exercise the apply_narration_result + intent fetch chain.
"""
from __future__ import annotations

from sidequest.game.session import GameSnapshot
from sidequest.handlers.course_intent import handle_course_sidecar
from sidequest.orbital.course import compute_courses
from sidequest.orbital.intent import handle_orbital_intent
from sidequest.orbital.loader import OrbitalContent
from sidequest.orbital.models import (
    BodyDef,
    BodyType,
    ChartConfig,
    ClockConfig,
    OrbitsConfig,
    TravelConfig,
    TravelRealism,
)
from sidequest.protocol.course_intent import (
    PlotCourseSidecar,
    parse_course_sidecar,
)
from sidequest.protocol.orbital_intent import OrbitalIntent
from sidequest.server.session import Session


def _orbits() -> OrbitsConfig:
    return OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(),
        travel=TravelConfig(realism=TravelRealism.ORBITAL),
        bodies={
            "coyote": BodyDef(type=BodyType.STAR),
            "near": BodyDef(
                type=BodyType.HABITAT,
                parent="coyote",
                semi_major_au=1.0,
                period_days=365.0,
                epoch_phase_deg=0.0,
            ),
            "far": BodyDef(
                type=BodyType.HABITAT,
                parent="coyote",
                semi_major_au=3.0,
                period_days=1100.0,
                epoch_phase_deg=180.0,
            ),
        },
    )


def _content() -> OrbitalContent:
    return OrbitalContent(orbits=_orbits(), chart=ChartConfig(version="0.1.0"))


def test_narrator_payload_to_chart_overlay() -> None:
    """Simulate a narrator game_patch with plot_course intent, apply,
    then re-render the chart and assert the overlay appears."""
    snapshot = GameSnapshot(party_body_id="near", clock_t_hours=0.0)
    session = Session(snapshot, orbital_content=_content())

    # Narrator-style game_patch payload (subset; only the bit we care about).
    game_patch = {"intent": "plot_course", "course_id": "far"}
    sidecar = parse_course_sidecar(game_patch)
    assert isinstance(sidecar, PlotCourseSidecar)

    in_scope = {"near", "far"}
    available = compute_courses(
        orbits=_orbits(),
        party_at="near",
        in_scope_body_ids=in_scope,
        recent_body_mentions=[],
        quest_anchors=[],
    )
    result = handle_course_sidecar(
        sidecar=sidecar,
        snapshot=snapshot,
        available_courses=available,
    )
    assert result.accepted
    assert snapshot.plotted_course is not None

    # Re-render the chart; verify overlay present.
    response = handle_orbital_intent(
        session,
        OrbitalIntent.model_validate(
            {"kind": "view_map", "scope": "system_root"}
        ),
    )
    assert "<path" in response.svg
    assert "stroke-dasharray" in response.svg
    assert "ETA" in response.svg


def test_cancel_course_drops_overlay() -> None:
    snapshot = GameSnapshot(party_body_id="near", clock_t_hours=0.0)
    session = Session(snapshot, orbital_content=_content())

    # Pre-set a course
    available = compute_courses(
        orbits=_orbits(),
        party_at="near",
        in_scope_body_ids={"near", "far"},
        recent_body_mentions=[],
        quest_anchors=[],
    )
    handle_course_sidecar(
        sidecar=PlotCourseSidecar(course_id="far"),
        snapshot=snapshot,
        available_courses=available,
    )
    assert snapshot.plotted_course is not None

    cancel = parse_course_sidecar({"intent": "cancel_course"})
    assert cancel is not None
    handle_course_sidecar(
        sidecar=cancel,
        snapshot=snapshot,
        available_courses=available,
    )
    assert snapshot.plotted_course is None

    response = handle_orbital_intent(
        session,
        OrbitalIntent.model_validate({"kind": "view_map", "scope": "system_root"}),
    )
    # Without a plotted_course, the overlay path must NOT be present.
    assert "course-overlay" not in response.svg
