"""Tests for ``ScenarioState.discover_clue`` DAG prerequisite enforcement — Story 50-6.

Hardens the entry point from ADR-053 §"Implementation status": a clue can
only be discovered after every clue it ``requires`` is already in
``discovered_clues``. Orphan discoveries (prereqs unsatisfied) raise
:class:`PrerequisiteNotSatisfiedError` with the missing clue ids in the
error detail, and emit ``SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION`` for
GM-panel observability.

Scope notes:
- Spec text says ``ClueGraph.edges: dict[str, list[str]]`` but the
  live data model is ``ClueNode.requires: list[str]`` on each node in
  ``ClueGraph.nodes``. Tests honour the live shape; a deviation is
  logged in the session.
- A clue id NOT in ``clue_graph.nodes`` is passed through unchanged
  (preserves the existing idempotency contract used by tests with an
  empty/absent graph and by 50-5's dispatch which pre-filters).
"""

from __future__ import annotations

import pytest

from sidequest.game.scenario_state import ScenarioState
from sidequest.genre.models.scenario import ClueGraph, ClueNode
from sidequest.telemetry.spans.scenario import SPAN_SCENARIO_ADVANCE
from tests.server.conftest import span_attrs_by_name

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _node(node_id: str, *, requires: list[str] | None = None) -> ClueNode:
    return ClueNode(
        id=node_id,
        type="testimony",
        description=f"clue {node_id}",
        discovery_method="conversation",
        visibility="public",
        requires=requires or [],
    )


def _state(*nodes: ClueNode) -> ScenarioState:
    return ScenarioState(clue_graph=ClueGraph(nodes=list(nodes)))


def _violation_events(exporter) -> list[dict]:
    from sidequest.telemetry.spans.scenario import (
        SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION,
    )

    return span_attrs_by_name(exporter, SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION)


# ---------------------------------------------------------------------------
# DAG enforcement — happy and orphan paths
# ---------------------------------------------------------------------------


class TestDAGPrerequisiteEnforcement:
    def test_root_clue_with_no_prerequisites_discovers(self) -> None:
        state = _state(_node("root"))
        state.discover_clue("root")
        assert state.discovered_clues == {"root"}

    def test_clue_with_satisfied_prerequisites_discovers(self) -> None:
        state = _state(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )
        state.discover_clue("body_found")
        state.discover_clue("murder_weapon")
        assert state.discovered_clues == {"body_found", "murder_weapon"}

    def test_orphan_discovery_raises_prerequisite_not_satisfied_error(self) -> None:
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )

        with pytest.raises(PrerequisiteNotSatisfiedError):
            state.discover_clue("murder_weapon")

    def test_orphan_discovery_does_not_add_to_discovered_clues(self) -> None:
        """Headline guarantee of ADR-053: orphan attempts must not leak into state."""
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )

        with pytest.raises(PrerequisiteNotSatisfiedError):
            state.discover_clue("murder_weapon")

        assert "murder_weapon" not in state.discovered_clues
        assert state.discovered_clues == set()

    def test_error_lists_missing_prerequisites(self) -> None:
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("body_found"),
            _node("witness_seen"),
            _node("verdict", requires=["body_found", "witness_seen"]),
        )

        with pytest.raises(PrerequisiteNotSatisfiedError) as excinfo:
            state.discover_clue("verdict")

        assert set(excinfo.value.missing_prerequisites) == {"body_found", "witness_seen"}
        assert excinfo.value.clue_id == "verdict"

    def test_error_lists_only_unsatisfied_prerequisites(self) -> None:
        """If one of two prereqs is already discovered, only the other is missing."""
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("body_found"),
            _node("witness_seen"),
            _node("verdict", requires=["body_found", "witness_seen"]),
        )
        state.discover_clue("body_found")

        with pytest.raises(PrerequisiteNotSatisfiedError) as excinfo:
            state.discover_clue("verdict")

        assert excinfo.value.missing_prerequisites == ["witness_seen"]

    def test_multi_level_chain_rejects_skipping_the_middle(self) -> None:
        """A -> B -> C: attempting C without B raises, even if A is satisfied."""
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("a"),
            _node("b", requires=["a"]),
            _node("c", requires=["b"]),
        )
        state.discover_clue("a")

        with pytest.raises(PrerequisiteNotSatisfiedError) as excinfo:
            state.discover_clue("c")

        assert excinfo.value.missing_prerequisites == ["b"]
        assert state.discovered_clues == {"a"}

    def test_multi_level_chain_in_order_discovers_all(self) -> None:
        state = _state(
            _node("a"),
            _node("b", requires=["a"]),
            _node("c", requires=["b"]),
        )
        state.discover_clue("a")
        state.discover_clue("b")
        state.discover_clue("c")
        assert state.discovered_clues == {"a", "b", "c"}

    def test_clue_outside_graph_passes_through_unchanged(self) -> None:
        """Backward-compat: a clue id absent from clue_graph.nodes is treated as
        having no declared prerequisites — preserves the empty-graph idempotency
        contract used by existing tests and by 50-5 dispatch's pre-filter path."""
        state = ScenarioState()  # empty graph
        state.discover_clue("unknown")
        assert "unknown" in state.discovered_clues

    def test_idempotent_rediscovery_does_not_raise(self) -> None:
        """A clue already in discovered_clues is re-discoverable without error.

        The DAG check sees its prereqs satisfied (because the clue itself was
        added on first discovery, so any forward-chain check still passes for
        downstream clues). Re-discovery of the clue itself must not raise.
        """
        state = _state(_node("root"))
        state.discover_clue("root")
        state.discover_clue("root")  # should not raise
        assert state.discovered_clues == {"root"}


# ---------------------------------------------------------------------------
# OTEL: violation span + advance span gating
# ---------------------------------------------------------------------------


class TestPrerequisiteViolationSpan:
    def test_violation_emits_dedicated_span(self, otel_exporter) -> None:
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )

        with pytest.raises(PrerequisiteNotSatisfiedError):
            state.discover_clue("murder_weapon")

        events = _violation_events(otel_exporter)
        assert len(events) == 1, (
            f"expected exactly one violation span, got {len(events)}"
        )
        attrs = events[0]
        assert attrs["clue_id"] == "murder_weapon"
        # missing_prerequisites is serialised as JSON-list-ish (OTEL attrs
        # are flat) — assert the body_found id is in the payload regardless
        # of the concrete encoding (list, tuple, or JSON string).
        missing = attrs["missing_prerequisites"]
        if isinstance(missing, str):
            assert "body_found" in missing
        else:
            assert "body_found" in list(missing)

    def test_violation_does_not_emit_scenario_advance(self, otel_exporter) -> None:
        """Lie-detector: rejection must NOT pretend to advance the scenario."""
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )

        with pytest.raises(PrerequisiteNotSatisfiedError):
            state.discover_clue("murder_weapon")

        assert span_attrs_by_name(otel_exporter, SPAN_SCENARIO_ADVANCE) == []

    def test_successful_discovery_emits_advance_not_violation(
        self, otel_exporter
    ) -> None:
        state = _state(_node("root"))
        state.discover_clue("root")

        assert len(span_attrs_by_name(otel_exporter, SPAN_SCENARIO_ADVANCE)) == 1
        assert _violation_events(otel_exporter) == []


# ---------------------------------------------------------------------------
# Exception shape contract
# ---------------------------------------------------------------------------


class TestExceptionContract:
    def test_error_exposes_clue_id_attribute(self) -> None:
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("a"),
            _node("b", requires=["a"]),
        )

        with pytest.raises(PrerequisiteNotSatisfiedError) as excinfo:
            state.discover_clue("b")

        assert excinfo.value.clue_id == "b"

    def test_error_missing_prerequisites_is_a_list(self) -> None:
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("a"),
            _node("b", requires=["a"]),
        )

        with pytest.raises(PrerequisiteNotSatisfiedError) as excinfo:
            state.discover_clue("b")

        assert isinstance(excinfo.value.missing_prerequisites, list)
        assert excinfo.value.missing_prerequisites == ["a"]

    def test_error_str_includes_clue_id_and_missing(self) -> None:
        """A descriptive __str__ helps GM-panel triage when logs are surfaced."""
        from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError

        state = _state(
            _node("a"),
            _node("b", requires=["a"]),
        )

        with pytest.raises(PrerequisiteNotSatisfiedError) as excinfo:
            state.discover_clue("b")

        msg = str(excinfo.value)
        assert "b" in msg
        assert "a" in msg
