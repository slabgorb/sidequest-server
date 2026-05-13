"""Dispatch handler must catch ``PrerequisiteNotSatisfiedError`` — Story 50-6.

The 50-5 helper :func:`consume_clue_footnotes` calls
:meth:`ScenarioState.discover_clue`. With 50-6's DAG enforcement, that
call can now raise :class:`PrerequisiteNotSatisfiedError` mid-batch.
The dispatch handler MUST:

- Not crash the turn (no exception propagates from the helper)
- Not mint a ``KnownFact`` for the rejected clue
- Not add the rejected clue to ``discovered_clues``
- Not emit ``SPAN_SCENARIO_ADVANCE`` for the rejected clue
- Continue processing the remaining footnotes in the batch — one orphan
  doesn't poison subsequent valid discoveries.

The violation span fires from ``discover_clue`` itself (consistent with
SPAN_SCENARIO_ADVANCE being owned at the data layer); the dispatch
handler is asserted to swallow the typed error and continue.
"""

from __future__ import annotations

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.scenario_state import ScenarioState
from sidequest.game.session import GameSnapshot
from sidequest.genre.models.scenario import ClueGraph, ClueNode
from sidequest.protocol.models import Footnote
from sidequest.telemetry.spans.scenario import SPAN_SCENARIO_ADVANCE
from tests.server.conftest import span_attrs_by_name


def _node(node_id: str, *, requires: list[str] | None = None) -> ClueNode:
    return ClueNode(
        id=node_id,
        type="testimony",
        description=f"clue {node_id}",
        discovery_method="conversation",
        visibility="public",
        requires=requires or [],
    )


def _character(name: str = "Rux") -> Character:
    return Character(
        core=CreatureCore(
            name=name,
            description="placeholder",
            personality="stoic",
            inventory=Inventory(),
        ),
        char_class="Fighter",
        race="Human",
        backstory="placeholder",
    )


def _snap_with_nodes(*nodes: ClueNode, interaction: int = 7) -> GameSnapshot:
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.characters.append(_character("Rux"))
    snap.turn_manager.interaction = interaction
    snap.scenario_state = ScenarioState(clue_graph=ClueGraph(nodes=list(nodes)))
    return snap


def _footnote(*, summary: str, fact_id: str | None, marker: int = 1) -> Footnote:
    return Footnote(
        marker=marker,
        fact_id=fact_id,
        summary=summary,
        category="Lore",
        is_new=True,
    )


def _violation_events(exporter) -> list[dict]:
    from sidequest.telemetry.spans.scenario import (
        SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION,
    )

    return span_attrs_by_name(exporter, SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION)


# ---------------------------------------------------------------------------
# Dispatch handler must swallow the typed error
# ---------------------------------------------------------------------------


class TestDispatchSwallowsPrerequisiteError:
    def test_orphan_footnote_does_not_crash_dispatch(self) -> None:
        """Headline guarantee: a DAG violation must not propagate out of the helper."""
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snap_with_nodes(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )
        footnotes = [_footnote(summary="The dagger.", fact_id="murder_weapon")]

        # Must not raise.
        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

    def test_orphan_footnote_does_not_mint_knownfact(self) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snap_with_nodes(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )
        footnotes = [_footnote(summary="The dagger.", fact_id="murder_weapon")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        assert snap.characters[0].known_facts == [], (
            "rejected clue must not mint a KnownFact into the journal"
        )

    def test_orphan_footnote_does_not_add_to_discovered_clues(self) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snap_with_nodes(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )
        footnotes = [_footnote(summary="The dagger.", fact_id="murder_weapon")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        assert snap.scenario_state is not None
        assert "murder_weapon" not in snap.scenario_state.discovered_clues


# ---------------------------------------------------------------------------
# OTEL: violation span fires; advance span does not
# ---------------------------------------------------------------------------


class TestDispatchViolationObservability:
    def test_orphan_footnote_emits_violation_span(self, otel_exporter) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snap_with_nodes(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )
        footnotes = [_footnote(summary="The dagger.", fact_id="murder_weapon")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        events = _violation_events(otel_exporter)
        assert len(events) == 1, (
            f"expected one violation span, got {len(events)}: {events}"
        )
        attrs = events[0]
        assert attrs["clue_id"] == "murder_weapon"
        missing = attrs["missing_prerequisites"]
        if isinstance(missing, str):
            assert "body_found" in missing
        else:
            assert "body_found" in list(missing)

    def test_orphan_footnote_does_not_emit_advance(self, otel_exporter) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snap_with_nodes(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )
        footnotes = [_footnote(summary="The dagger.", fact_id="murder_weapon")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        assert span_attrs_by_name(otel_exporter, SPAN_SCENARIO_ADVANCE) == []


# ---------------------------------------------------------------------------
# Batch continuity — one orphan does not poison subsequent valid footnotes
# ---------------------------------------------------------------------------


class TestBatchContinuity:
    def test_valid_footnote_after_orphan_still_processes(self, otel_exporter) -> None:
        """If the narrator emits [orphan, root] in one batch, the root clue
        must still be discovered. The dispatch handler must not bail on the
        first error."""
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snap_with_nodes(
            _node("body_found"),
            _node("murder_weapon", requires=["body_found"]),
        )
        footnotes = [
            _footnote(summary="The dagger.", fact_id="murder_weapon", marker=1),
            _footnote(summary="A body in the library.", fact_id="body_found", marker=2),
        ]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        # body_found discovered; murder_weapon rejected.
        assert snap.scenario_state is not None
        assert snap.scenario_state.discovered_clues == {"body_found"}

        # KnownFact minted only for the successful discovery.
        rux = snap.characters[0]
        assert len(rux.known_facts) == 1
        assert rux.known_facts[0].content == "A body in the library."
        assert rux.known_facts[0].source == "ScenarioClue"

        # One advance span (body_found), one violation span (murder_weapon).
        assert len(span_attrs_by_name(otel_exporter, SPAN_SCENARIO_ADVANCE)) == 1
        assert len(_violation_events(otel_exporter)) == 1

    def test_orphan_in_middle_does_not_stop_trailing_valid(
        self, otel_exporter
    ) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snap_with_nodes(
            _node("a"),
            _node("b", requires=["a"]),
            _node("c"),
        )
        # Order: c (root, valid) → b (orphan, a missing) → a (root, valid)
        footnotes = [
            _footnote(summary="finding C.", fact_id="c", marker=1),
            _footnote(summary="finding B.", fact_id="b", marker=2),
            _footnote(summary="finding A.", fact_id="a", marker=3),
        ]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        assert snap.scenario_state is not None
        assert snap.scenario_state.discovered_clues == {"a", "c"}
        rux = snap.characters[0]
        summaries = {kf.content for kf in rux.known_facts}
        assert summaries == {"finding A.", "finding C."}
