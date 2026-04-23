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
from sidequest.telemetry.spans import projection_decide_span


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
        with projection_decide_span(
            event_kind=envelope.kind,
            event_seq=envelope.origin_seq,
            player_id=player_id,
        ) as span:
            outcome = self._invariants.evaluate(
                envelope=envelope, view=view, player_id=player_id
            )
            if outcome.terminal:
                assert outcome.decision is not None
                decision = outcome.decision
                source = _invariant_source(envelope=envelope, view=view, player_id=player_id)
            else:
                decision = self._genre.evaluate(
                    envelope=envelope, view=view, player_id=player_id
                )
                source = (
                    f"genre:{envelope.kind}"
                    if envelope.kind in self._genre._by_kind
                    else "default:pass_through"
                )
            span.set_attribute("decision.include", decision.include)
            span.set_attribute("rule.source", source)
            return decision

    @classmethod
    def with_no_genre_rules(cls) -> "ComposedFilter":
        """Convenience for sessions whose genre pack has no projection.yaml."""
        return cls(rules=load_rules_from_yaml_str("rules: []"))


def _invariant_source(
    *, envelope: MessageEnvelope, view: GameStateView, player_id: str
) -> str:
    if view.is_gm(player_id):
        return "invariant:gm_sees_all"
    from sidequest.game.projection.invariants import (
        GM_ONLY_KINDS,
        SELF_AUTHORED_KINDS,
        TARGETED_KINDS,
    )
    if envelope.kind in TARGETED_KINDS:
        return "invariant:targeted"
    if envelope.kind in SELF_AUTHORED_KINDS:
        return "invariant:self_echo"
    if envelope.kind in GM_ONLY_KINDS:
        return "invariant:gm_only_kind"
    return "invariant:unknown"
