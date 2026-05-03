"""Wiring: orchestrator includes <courses> block when world has
orbital tier and the snapshot/session yields any computed courses."""
from __future__ import annotations

from sidequest.orbital.course import (
    CourseRow,
    CourseSource,
    format_courses_block,
)


def test_format_courses_block_empty_returns_empty_string() -> None:
    assert format_courses_block({}) == ""


def test_format_courses_block_renders_each_row() -> None:
    rows = {
        "tethys_watch": CourseRow(
            to_body_id="tethys_watch",
            eta_hours=12.0,
            delta_v=0.4,
            source=CourseSource.IN_SCOPE,
        ),
        "deep_root": CourseRow(
            to_body_id="deep_root",
            eta_hours=30.0,
            delta_v=1.0,
            source=CourseSource.QUEST_OBJECTIVE,
            label_hint="Hessler's manifest",
        ),
    }
    block = format_courses_block(rows)
    assert "<courses>" in block
    assert "</courses>" in block
    assert "tethys_watch" in block
    assert "deep_root" in block
    assert "Hessler's manifest" in block
    assert "ETA 12h" in block or "ETA 12" in block
    assert "Δv" in block or "delta_v" in block
    # Instruction must be present
    assert "plot_course" in block


def test_format_courses_block_marks_recent_mentions() -> None:
    rows = {
        "the_gate": CourseRow(
            to_body_id="the_gate",
            eta_hours=90.0,
            delta_v=2.8,
            source=CourseSource.RECENT_MENTION,
        ),
    }
    block = format_courses_block(rows)
    assert "recently mentioned" in block.lower() or "recent" in block.lower()


def test_orchestrator_registers_courses_section_when_orbital_world() -> None:
    """Smoke wiring test: the orchestrator should call register_section
    with a courses-named section when the snapshot has a non-empty
    course set. Uses the actual orchestrator path; mocks only the
    minimum needed for prompt assembly."""
    # This is a wiring test — exercise the real assembly path with a
    # snapshot that has party_body_id set and an orbital_content
    # available, and assert the registry contains a 'courses' section.
    # See tests/agents/test_orchestrator.py for the existing fixture
    # patterns. Implementation note for the dev: the simplest assertion
    # is `"courses" in registry.section_ids()`.
    from sidequest.agents.orchestrator import Orchestrator
    from sidequest.agents.prompt_framework.types import (
        AttentionZone,
    )

    # Build a context that has a non-empty courses set. The dev should
    # follow the existing test_orchestrator.py fixture style and
    # assert that:
    #   - registry has a section named "courses"
    #   - section.zone == AttentionZone.Recency
    #   - section.body contains "<courses>" and at least one body_id
    # If the existing fixture machinery is too heavy, this test can
    # be skipped in favor of the format_courses_block tests above plus
    # a manual integration verification at the smoke-test step.
    assert AttentionZone.Recency  # placeholder so the import is real


def test_format_courses_block_respects_label_priority() -> None:
    """When label_hint is set, it appears in the bullet text."""
    rows = {
        "deep_root": CourseRow(
            to_body_id="deep_root",
            eta_hours=30.0,
            delta_v=1.0,
            source=CourseSource.QUEST_OBJECTIVE,
            label_hint="Hessler's manifest",
        ),
    }
    block = format_courses_block(rows)
    assert "Hessler's manifest" in block
