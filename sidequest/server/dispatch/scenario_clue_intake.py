"""Scenario clue intake — ADR-100 seams A and B (Story 50-5).

Bridges the narrator's :class:`~sidequest.protocol.models.Footnote` stream
to the bound :class:`~sidequest.game.scenario_state.ScenarioState`:

- **Seam A — advance.** When a footnote's ``fact_id`` matches a
  ``ClueNode.id`` in the scenario's ``clue_graph``, call
  :meth:`ScenarioState.discover_clue`, which fires
  ``SPAN_SCENARIO_ADVANCE``. The subsystem owns its own duplicate-flag
  attribute; this helper does not gate the span.
- **Seam B — mint.** On the *first* discovery of a given clue, append a
  ``KnownFact`` to the named active character with
  ``confidence='Discovered'``, ``source='ScenarioClue'``, and
  ``learned_turn`` taken from ``snapshot.turn_manager.interaction``.
  Re-discovery of the same clue does NOT mint a second fact — the
  character journal must not spam Sebastien's GM panel with duplicate
  entries every time the narrator re-cites the same evidence.

Sibling of :mod:`sidequest.server.dispatch.scenario_bind`, which wires
the scenario at chargen confirmation; this module wires it at narration
consumption.
"""

from __future__ import annotations

from collections.abc import Iterable

from sidequest.game.character import KnownFact
from sidequest.game.scenario_state import PrerequisiteNotSatisfiedError
from sidequest.game.session import GameSnapshot
from sidequest.protocol.models import Footnote


def consume_clue_footnotes(
    snapshot: GameSnapshot,
    footnotes: Iterable[Footnote],
    active_character_name: str,
) -> None:
    """Discover any scenario clues referenced by ``footnotes`` and mint a
    ``KnownFact`` on the named active character for each first-time
    discovery.

    A footnote whose ``fact_id`` resolves to a clue whose DAG
    prerequisites are not yet satisfied is rejected:
    :meth:`ScenarioState.discover_clue` raises
    :class:`PrerequisiteNotSatisfiedError` and emits the
    ``SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION`` span itself. This
    handler catches the typed error and continues with remaining
    footnotes — one orphan must not poison the batch.
    """
    scenario = snapshot.scenario_state
    if scenario is None:
        return

    clue_ids = {node.id for node in scenario.clue_graph.nodes}
    active = next(
        (c for c in snapshot.characters if c.core.name == active_character_name),
        None,
    )

    for fn in footnotes:
        if fn.fact_id is None or fn.fact_id not in clue_ids:
            continue
        is_new = fn.fact_id not in scenario.discovered_clues
        try:
            scenario.discover_clue(fn.fact_id)
        except PrerequisiteNotSatisfiedError:
            # Violation span already emitted at the data layer; skip
            # the KnownFact mint and move on to the next footnote.
            continue
        if is_new and active is not None:
            active.known_facts.append(
                KnownFact(
                    # NonBlankString is a Pydantic RootModel, not a str subclass —
                    # str() unwraps the .root for KnownFact.content: str.
                    content=str(fn.summary),
                    confidence="Discovered",
                    source="ScenarioClue",
                    learned_turn=snapshot.turn_manager.interaction,
                    # 50-14: propagate the clue id so the journal UI can
                    # dedup across JOURNAL_RESPONSE replays. fn.fact_id is
                    # guaranteed non-None at this point per line 62 above.
                    fact_id=fn.fact_id,
                    category=fn.category,
                )
            )


__all__ = ["consume_clue_footnotes"]
