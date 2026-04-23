"""GenreRuleStage — applies genre-configured projection.yaml rules.

Runs AFTER CoreInvariantStage. Rules for a given kind are applied in
document order. Include-gates (target_only / include_if) that evaluate
False short-circuit the remaining rules for that envelope; passing
include-gates continue, allowing redact_fields rules to mask specific
fields on the still-included viewer's projection.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.field_path import apply_mask
from sidequest.game.projection.predicates import PREDICATES, PredicateContext
from sidequest.game.projection.rules import (
    IncludeIfRule,
    ProjectionRules,
    RedactFieldsRule,
    TargetOnlyRule,
)
from sidequest.game.projection.view import GameStateView
from sidequest.game.projection_filter import FilterDecision


@dataclass(frozen=True)
class GenreEvalResult:
    """Result of running ``GenreRuleStage.evaluate`` on one envelope.

    ``matched_rule_index`` is the global index (position in
    ``ProjectionRules.rules``) of the last rule that actually affected the
    decision — the include-gate that dropped, or the redact_fields rule
    whose unless-predicate failed and masked a field.

    ``None`` when no genre rule existed for the kind (pass-through) or
    when rules existed but none actually altered the payload (all gates
    passed, no mask applied). Callers emit ``default:pass_through`` in
    those cases.

    Ordering dependency: ``payload`` and ``working`` are the same dict
    inside ``evaluate`` — ``apply_mask`` mutates in place, so a
    ``TargetOnlyRule`` that appears *after* a ``RedactFieldsRule`` for
    the same kind will read the already-mutated dict. No current rule
    shape exercises this because TargetOnlyRule only reads the ``to``
    field and no rule currently redacts ``to``. Flagged as latent.
    """

    decision: FilterDecision
    matched_rule_index: int | None


class GenreRuleStage:
    def __init__(self, rules: ProjectionRules) -> None:
        # _by_kind stores (global_rule_index, rule) tuples so we can report
        # the fired rule's position in the original projection.yaml list for
        # OTEL ``rule.source`` attribution.
        self._by_kind: dict[str, list[tuple[int, object]]] = {}
        for idx, r in enumerate(rules.rules):
            self._by_kind.setdefault(r.kind, []).append((idx, r))

    def evaluate(
        self,
        *,
        envelope: MessageEnvelope,
        view: GameStateView,
        player_id: str,
    ) -> GenreEvalResult:
        rules = self._by_kind.get(envelope.kind, [])
        if not rules:
            return GenreEvalResult(
                decision=FilterDecision(include=True, payload_json=envelope.payload_json),
                matched_rule_index=None,
            )

        payload = json.loads(envelope.payload_json)
        # Ordering note: ``working is payload`` — apply_mask mutates in place.
        # See class docstring for the latent cross-rule ordering dependency
        # (TargetOnlyRule after RedactFieldsRule reads post-mask payload).
        working = payload
        last_applied_idx: int | None = None

        for rule_idx, rule in rules:
            if isinstance(rule, TargetOnlyRule):
                to_value = payload.get(rule.target_only.field)
                if not _match_to_value(to_value, player_id):
                    return GenreEvalResult(
                        decision=FilterDecision(include=False, payload_json=""),
                        matched_rule_index=rule_idx,
                    )

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
                    return GenreEvalResult(
                        decision=FilterDecision(include=False, payload_json=""),
                        matched_rule_index=rule_idx,
                    )

            if isinstance(rule, RedactFieldsRule):
                ctx = PredicateContext(
                    view=view,
                    payload=payload,
                    viewer_player_id=player_id,
                    viewer_character_id=view.character_of(player_id),
                )
                for spec in rule.redact_fields:
                    pred = PREDICATES.get(spec.unless.predicate)
                    if pred is None:
                        raise RuntimeError(
                            f"unknown predicate {spec.unless.predicate!r} at runtime"
                        )
                    if not pred(ctx, spec.unless.arg):
                        apply_mask(working, spec.field, mask=spec.mask)
                        last_applied_idx = rule_idx

        return GenreEvalResult(
            decision=FilterDecision(include=True, payload_json=json.dumps(working)),
            matched_rule_index=last_applied_idx,
        )


def _match_to_value(to_value: object, player_id: str) -> bool:
    if isinstance(to_value, str):
        return to_value == player_id
    if isinstance(to_value, list):
        return player_id in to_value
    return False
