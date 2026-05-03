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


def _mini_orbits() -> "OrbitsConfig":
    """Tiny orbits config: coyote + 4 habitats at 1, 2, 3, 4 AU."""
    from sidequest.orbital.models import (
        BodyDef,
        BodyType,
        ClockConfig,
        OrbitsConfig,
        TravelConfig,
        TravelRealism,
    )

    bodies = {
        "coyote": BodyDef(type=BodyType.STAR),
        "near": BodyDef(
            type=BodyType.HABITAT,
            parent="coyote",
            semi_major_au=1.0,
            period_days=365.0,
            epoch_phase_deg=0.0,
        ),
        "mid": BodyDef(
            type=BodyType.HABITAT,
            parent="coyote",
            semi_major_au=2.0,
            period_days=720.0,
            epoch_phase_deg=90.0,
        ),
        "far": BodyDef(
            type=BodyType.HABITAT,
            parent="coyote",
            semi_major_au=3.0,
            period_days=1100.0,
            epoch_phase_deg=180.0,
        ),
        "edge": BodyDef(
            type=BodyType.HABITAT,
            parent="coyote",
            semi_major_au=4.0,
            period_days=1500.0,
            epoch_phase_deg=270.0,
        ),
    }
    return OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(),
        travel=TravelConfig(realism=TravelRealism.ORBITAL, travel_speed_factor=1.0),
        bodies=bodies,
    )


def test_compute_courses_excludes_party_at() -> None:
    from sidequest.orbital.course import compute_courses

    rows = compute_courses(
        orbits=_mini_orbits(),
        party_at="near",
        in_scope_body_ids={"near", "mid", "far", "edge"},
        recent_body_mentions=[],
        quest_anchors=[],
    )
    assert "near" not in rows


def test_compute_courses_in_scope_source() -> None:
    from sidequest.orbital.course import CourseSource, compute_courses

    rows = compute_courses(
        orbits=_mini_orbits(),
        party_at="near",
        in_scope_body_ids={"near", "mid"},
        recent_body_mentions=[],
        quest_anchors=[],
    )
    assert set(rows.keys()) == {"mid"}
    assert rows["mid"].source == CourseSource.IN_SCOPE


def test_compute_courses_recent_mention_overrides_in_scope_priority() -> None:
    from sidequest.orbital.course import CourseSource, compute_courses

    # mid is both in-scope AND recently mentioned → recent_mention wins
    # (higher priority).
    rows = compute_courses(
        orbits=_mini_orbits(),
        party_at="near",
        in_scope_body_ids={"mid"},
        recent_body_mentions=["mid"],
        quest_anchors=[],
    )
    assert rows["mid"].source == CourseSource.RECENT_MENTION


def test_compute_courses_quest_objective_top_priority() -> None:
    from sidequest.orbital.course import CourseSource, compute_courses

    rows = compute_courses(
        orbits=_mini_orbits(),
        party_at="near",
        in_scope_body_ids={"mid"},
        recent_body_mentions=["mid"],
        quest_anchors=["mid"],
    )
    assert rows["mid"].source == CourseSource.QUEST_OBJECTIVE


def test_compute_courses_skips_unknown_body_ids_silently() -> None:
    """Unknown ids in inputs are dropped; we never invent bodies."""
    from sidequest.orbital.course import compute_courses

    rows = compute_courses(
        orbits=_mini_orbits(),
        party_at="near",
        in_scope_body_ids={"unknown_body"},
        recent_body_mentions=["also_unknown"],
        quest_anchors=["still_unknown"],
    )
    assert rows == {}


def test_compute_courses_caps_at_12() -> None:
    """Lots of in-scope bodies + a couple of higher-priority entries —
    cap drops in-scope first, keeps quest + recent."""
    from sidequest.orbital.course import compute_courses
    from sidequest.orbital.models import BodyDef, BodyType

    # Build orbits with party + 14 habitats so we exceed the cap.
    big_bodies = {"coyote": BodyDef(type=BodyType.STAR)}
    for i in range(14):
        big_bodies[f"hab_{i:02d}"] = BodyDef(
            type=BodyType.HABITAT,
            parent="coyote",
            semi_major_au=1.0 + 0.5 * i,
            period_days=365.0 + 100 * i,
            epoch_phase_deg=(i * 25) % 360,
        )
    big_bodies["party_body"] = BodyDef(
        type=BodyType.HABITAT,
        parent="coyote",
        semi_major_au=0.5,
        period_days=200.0,
        epoch_phase_deg=0.0,
    )
    from sidequest.orbital.models import (
        ClockConfig,
        OrbitsConfig,
        TravelConfig,
        TravelRealism,
    )

    orbits = OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(),
        travel=TravelConfig(realism=TravelRealism.ORBITAL),
        bodies=big_bodies,
    )

    rows = compute_courses(
        orbits=orbits,
        party_at="party_body",
        in_scope_body_ids={f"hab_{i:02d}" for i in range(14)},  # 14 in-scope
        recent_body_mentions=[],  # already represented in in_scope set
        quest_anchors=["hab_00"],  # 1 quest pin
    )
    assert len(rows) == 12
    # Quest must survive
    assert "hab_00" in rows
    # Quest source preserved
    from sidequest.orbital.course import CourseSource

    assert rows["hab_00"].source == CourseSource.QUEST_OBJECTIVE


def test_compute_courses_deterministic_order() -> None:
    """Same inputs → same output, including dict iteration order."""
    from sidequest.orbital.course import compute_courses

    a = compute_courses(
        orbits=_mini_orbits(),
        party_at="near",
        in_scope_body_ids={"mid", "far", "edge"},
        recent_body_mentions=[],
        quest_anchors=[],
    )
    b = compute_courses(
        orbits=_mini_orbits(),
        party_at="near",
        in_scope_body_ids={"edge", "far", "mid"},
        recent_body_mentions=[],
        quest_anchors=[],
    )
    assert list(a.keys()) == list(b.keys())


def test_compute_courses_label_hint_only_for_quest_objective() -> None:
    from sidequest.orbital.course import CourseSource, compute_courses

    # Without a label_hint map, even quest entries leave it None.
    rows = compute_courses(
        orbits=_mini_orbits(),
        party_at="near",
        in_scope_body_ids=set(),
        recent_body_mentions=[],
        quest_anchors=["mid"],
    )
    assert rows["mid"].source == CourseSource.QUEST_OBJECTIVE
    assert rows["mid"].label_hint is None
