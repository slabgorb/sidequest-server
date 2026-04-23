"""ComposedFilter — the production ProjectionFilter.

Pipeline: CoreInvariantStage (GM / targeted / self-authored / gm-only)
    → GenreRuleStage (projection.yaml rules)
    → default pass-through.
"""
from __future__ import annotations

from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.genre_stage import GenreRuleStage
from sidequest.game.projection.invariants import CoreInvariantStage
from sidequest.game.projection.rules import ProjectionRules, load_rules_from_yaml_str
from sidequest.game.projection.view import GameStateView
from sidequest.game.projection_filter import FilterDecision


class ComposedFilter:
    """Implements the ProjectionFilter Protocol."""

    def __init__(
        self,
        *,
        rules: ProjectionRules,
        invariants: CoreInvariantStage | None = None,
    ) -> None:
        self._invariants = invariants or CoreInvariantStage()
        self._genre = GenreRuleStage(rules)

    def project(
        self,
        *,
        envelope: MessageEnvelope,
        view: GameStateView,
        player_id: str,
    ) -> FilterDecision:
        outcome = self._invariants.evaluate(
            envelope=envelope, view=view, player_id=player_id
        )
        if outcome.terminal:
            assert outcome.decision is not None
            return outcome.decision
        return self._genre.evaluate(
            envelope=envelope, view=view, player_id=player_id
        )

    @classmethod
    def with_no_genre_rules(cls) -> "ComposedFilter":
        """Convenience for sessions whose genre pack has no projection.yaml."""
        return cls(rules=load_rules_from_yaml_str("rules: []"))
