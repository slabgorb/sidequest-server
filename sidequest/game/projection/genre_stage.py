"""GenreRuleStage — applies genre-configured projection.yaml rules.

Runs AFTER CoreInvariantStage. Rules for a given kind are applied in
document order. Include-gates (target_only / include_if) that evaluate
False short-circuit the remaining rules for that envelope; passing
include-gates continue, allowing redact_fields rules to mask specific
fields on the still-included viewer's projection.
"""
from __future__ import annotations

import json

from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.predicates import PREDICATES, PredicateContext
from sidequest.game.projection.rules import (
    IncludeIfRule,
    ProjectionRules,
    TargetOnlyRule,
)
from sidequest.game.projection.view import GameStateView
from sidequest.game.projection_filter import FilterDecision


class GenreRuleStage:
    def __init__(self, rules: ProjectionRules) -> None:
        self._by_kind: dict[str, list] = {}
        for r in rules.rules:
            self._by_kind.setdefault(r.kind, []).append(r)

    def evaluate(
        self,
        *,
        envelope: MessageEnvelope,
        view: GameStateView,
        player_id: str,
    ) -> FilterDecision:
        rules = self._by_kind.get(envelope.kind, [])
        if not rules:
            return FilterDecision(include=True, payload_json=envelope.payload_json)

        payload = json.loads(envelope.payload_json)
        working = payload

        for rule in rules:
            if isinstance(rule, TargetOnlyRule):
                to_value = payload.get(rule.target_only.field)
                if not _match_to_value(to_value, player_id):
                    return FilterDecision(include=False, payload_json="")

            if isinstance(rule, IncludeIfRule):
                pred = PREDICATES.get(rule.include_if.predicate)
                if pred is None:
                    raise RuntimeError(
                        f"unknown predicate {rule.include_if.predicate!r} "
                        f"at runtime (validator should have caught this)"
                    )
                ctx = PredicateContext(
                    view=view,
                    payload=payload,
                    viewer_player_id=player_id,
                    viewer_character_id=view.character_of(player_id),
                )
                if not pred(ctx, rule.include_if.arg):
                    return FilterDecision(include=False, payload_json="")

        return FilterDecision(include=True, payload_json=json.dumps(working))


def _match_to_value(to_value: object, player_id: str) -> bool:
    if isinstance(to_value, str):
        return to_value == player_id
    if isinstance(to_value, list):
        return player_id in to_value
    return False
