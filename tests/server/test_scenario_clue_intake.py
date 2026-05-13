"""Unit tests for scenario clue intake — Story 50-5 (ADR-100 seam A + B).

Covers the helper that consumes narrator-emitted ``Footnote`` objects
against the bound ``ScenarioState.clue_graph``:

- Seam A: ``ScenarioState.discover_clue`` is called when ``fact_id``
  matches a ``ClueNode.id`` (which fires ``SPAN_SCENARIO_ADVANCE``).
- Seam B: a ``KnownFact`` is appended to the active character with
  ``confidence='Discovered'``, ``source='ScenarioClue'``,
  ``learned_turn`` equal to the snapshot's interaction counter, and
  ``content`` equal to the footnote summary.

Negative paths (no scenario bound, no matching clue, no fact_id) and
duplicate-clue behavior are exercised explicitly — the journal must not
spam-mint a fresh ``KnownFact`` every time the narrator re-cites the
same clue.

This helper has no production callers yet — that wiring lands in the
``test_narration_clue_discovery_wiring.py`` integration test in the
same RED set.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.scenario_state import ScenarioState
from sidequest.game.session import GameSnapshot
from sidequest.genre.models.scenario import ClueGraph, ClueNode
from sidequest.protocol.models import Footnote
from sidequest.telemetry.spans.scenario import SPAN_SCENARIO_ADVANCE

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _clue_node(node_id: str) -> ClueNode:
    return ClueNode(
        id=node_id,
        type="testimony",
        description=f"clue {node_id}",
        discovery_method="conversation",
        visibility="public",
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


def _snapshot_with_scenario(
    *,
    clue_ids: list[str],
    interaction: int = 7,
    character_name: str = "Rux",
) -> GameSnapshot:
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.characters.append(_character(character_name))
    snap.turn_manager.interaction = interaction
    snap.scenario_state = ScenarioState(
        clue_graph=ClueGraph(nodes=[_clue_node(cid) for cid in clue_ids]),
    )
    return snap


def _footnote(*, summary: str, fact_id: str | None, marker: int = 1) -> Footnote:
    return Footnote(
        marker=marker,
        fact_id=fact_id,
        summary=summary,
        category="Lore",
        is_new=True,
    )


@pytest.fixture
def otel_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Install as the spans-module tracer so Span.open() picks it up.
    from sidequest.telemetry import spans as _spans

    original = _spans.tracer
    _spans.tracer = lambda: provider.get_tracer("test")
    try:
        yield exporter
    finally:
        _spans.tracer = original


def _scenario_advance_events(exporter: InMemorySpanExporter) -> list[dict]:
    """Return attribute dicts for every SPAN_SCENARIO_ADVANCE span recorded."""
    out: list[dict] = []
    for span in exporter.get_finished_spans():
        if span.name == SPAN_SCENARIO_ADVANCE:
            out.append(dict(span.attributes or {}))
    return out


# ---------------------------------------------------------------------------
# Happy path — seams A + B
# ---------------------------------------------------------------------------


class TestSeamA_DiscoverClueOnMatch:
    """Seam A: a matching fact_id fires SPAN_SCENARIO_ADVANCE exactly once."""

    def test_matching_fact_id_fires_scenario_advance_span(self, otel_exporter) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key", "muddy_boot"])
        footnotes = [
            _footnote(summary="The key fits the library door.", fact_id="library_key"),
        ]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        events = _scenario_advance_events(otel_exporter)
        assert len(events) == 1, (
            f"expected exactly one SPAN_SCENARIO_ADVANCE, got {len(events)}: {events}"
        )
        assert events[0]["clue_id"] == "library_key"
        assert events[0]["duplicate"] is False

    def test_matching_fact_id_adds_to_discovered_clues(self) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key"])
        footnotes = [_footnote(summary="x", fact_id="library_key")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        assert snap.scenario_state is not None
        assert "library_key" in snap.scenario_state.discovered_clues


class TestSeamB_MintKnownFact:
    """Seam B: discovery appends a typed KnownFact to the active character."""

    def test_known_fact_appended_to_active_character(self) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(
            clue_ids=["library_key"], interaction=42, character_name="Rux"
        )
        footnotes = [
            _footnote(
                summary="The key fits the library door.",
                fact_id="library_key",
            )
        ]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        rux = snap.characters[0]
        assert len(rux.known_facts) == 1, (
            f"expected exactly one KnownFact appended, got {len(rux.known_facts)}"
        )
        kf = rux.known_facts[0]
        assert kf.content == "The key fits the library door."
        assert kf.confidence == "Discovered"
        assert kf.source == "ScenarioClue"
        assert kf.learned_turn == 42

    def test_multiple_matches_mint_one_known_fact_each(self) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["a", "b", "c"])
        footnotes = [
            _footnote(summary="finding A", fact_id="a", marker=1),
            _footnote(summary="finding B", fact_id="b", marker=2),
        ]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        rux = snap.characters[0]
        assert len(rux.known_facts) == 2
        summaries = {kf.content for kf in rux.known_facts}
        assert summaries == {"finding A", "finding B"}

    def test_known_fact_targets_named_active_character_not_first(self) -> None:
        """In multiplayer the active player may not be characters[0]."""
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key"])
        # Add a second character; mint should target the *named* active one.
        snap.characters.append(_character("Mira"))
        footnotes = [_footnote(summary="The key fits.", fact_id="library_key")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Mira")

        rux = next(c for c in snap.characters if c.core.name == "Rux")
        mira = next(c for c in snap.characters if c.core.name == "Mira")
        assert rux.known_facts == [], "non-active character must not get the KnownFact"
        assert len(mira.known_facts) == 1
        assert mira.known_facts[0].content == "The key fits."


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


class TestNegativePaths:
    def test_no_scenario_bound_is_silent_noop(self, otel_exporter) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = GameSnapshot(genre_slug="caverns_and_claudes")
        snap.characters.append(_character("Rux"))
        assert snap.scenario_state is None
        footnotes = [_footnote(summary="x", fact_id="library_key")]

        # Must not raise, must not mint, must not span.
        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        assert snap.characters[0].known_facts == []
        assert _scenario_advance_events(otel_exporter) == []

    def test_fact_id_not_in_clue_graph_is_silent_noop(self, otel_exporter) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key"])
        # Narrator emitted a generic worldbuilding fact, not a scenario clue.
        footnotes = [_footnote(summary="The wind howls.", fact_id="weather_note")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        assert snap.characters[0].known_facts == []
        assert snap.scenario_state is not None
        assert snap.scenario_state.discovered_clues == set()
        assert _scenario_advance_events(otel_exporter) == []

    def test_footnote_without_fact_id_is_skipped(self, otel_exporter) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key"])
        footnotes = [_footnote(summary="just lore", fact_id=None)]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        assert snap.characters[0].known_facts == []
        assert _scenario_advance_events(otel_exporter) == []

    def test_empty_footnote_list_is_noop(self, otel_exporter) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key"])

        consume_clue_footnotes(snap, [], active_character_name="Rux")

        assert snap.characters[0].known_facts == []
        assert _scenario_advance_events(otel_exporter) == []

    def test_unknown_active_character_name_does_not_mint(self) -> None:
        """If active_character_name doesn't match any character, do not mint silently.

        Per CLAUDE.md "no silent fallbacks": a clue still discovers (subsystem
        fires), but the KnownFact append must not no-op onto characters[0] —
        that would mask a bug. Acceptable behaviors: skip the mint and log,
        or raise. This test asserts the mint does NOT land on a wrong character.
        """
        import contextlib

        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key"])
        footnotes = [_footnote(summary="The key.", fact_id="library_key")]

        # Loud failure is acceptable per "no silent fallbacks" doctrine — the
        # test only requires the KnownFact does not silently land on the wrong
        # character. Implementations may raise or skip; both are valid.
        with contextlib.suppress(KeyError, ValueError, LookupError):
            consume_clue_footnotes(snap, footnotes, active_character_name="Nobody")

        # The single existing character must not receive the KnownFact.
        rux = snap.characters[0]
        assert rux.known_facts == [], (
            "KnownFact must not silently land on the wrong character when the named active is missing"
        )


# ---------------------------------------------------------------------------
# Re-discovery / duplicate handling
# ---------------------------------------------------------------------------


class TestDuplicateHandling:
    """A clue already in discovered_clues must not double-mint a KnownFact.

    Re-discovery (narrator re-cites a previously revealed fact_id in a later
    turn) is a real case: the journal would spam if we appended every time.
    The subsystem's span fires either way (duplicate flag tracks it).
    """

    def test_second_discovery_of_same_clue_does_not_double_mint(self) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key"], interaction=5)
        footnotes = [_footnote(summary="The key.", fact_id="library_key")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")
        # Simulate a later turn re-citing the same clue.
        snap.turn_manager.interaction = 6
        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        rux = snap.characters[0]
        assert len(rux.known_facts) == 1, (
            "duplicate clue discovery must not mint a second KnownFact (journal spam)"
        )
        assert rux.known_facts[0].learned_turn == 5, (
            "the first-discovery turn must be preserved, not overwritten"
        )

    def test_second_discovery_still_advances_subsystem_span_with_duplicate_flag(
        self, otel_exporter
    ) -> None:
        from sidequest.server.dispatch.scenario_clue_intake import (
            consume_clue_footnotes,
        )

        snap = _snapshot_with_scenario(clue_ids=["library_key"])
        footnotes = [_footnote(summary="The key.", fact_id="library_key")]

        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")
        consume_clue_footnotes(snap, footnotes, active_character_name="Rux")

        events = _scenario_advance_events(otel_exporter)
        assert len(events) == 2, (
            f"expected two SPAN_SCENARIO_ADVANCE spans across two calls, got {len(events)}"
        )
        # First call: duplicate=False. Second call: duplicate=True.
        assert events[0]["duplicate"] is False
        assert events[1]["duplicate"] is True
