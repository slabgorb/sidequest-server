"""Core invariants — structural guarantees genre packs cannot weaken.

Runs before GenreRuleStage in the ComposedFilter. Can short-circuit with
a terminal decision (include=True with canonical payload, or include=False).

Invariants shipped in this stage:
    - GM sees canonical (Task 5).
    - Targeted-by-field — SECRET_NOTE / DICE_REQUEST / etc.'s `to` field
      restricts recipients (Task 6).
    - Self-authored — PLAYER_ACTION / DICE_THROW echo to author + GM
      (Task 7).
    - GM-only kind — THINKING is never routed to players (Task 8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.view import GameStateView
from sidequest.game.projection_filter import FilterDecision

# Kinds whose canonical payload carries a `to` field naming the recipient(s).
# The `to` value may be a single player_id string OR a list[str] of player_ids.
# GM is always an implicit recipient (added by the GM invariant above).
TARGETED_KINDS: dict[str, str] = {
    "SECRET_NOTE": "to",
    "DICE_REQUEST": "to",
    "JOURNAL_RESPONSE": "to",
    "VOICE_TEXT": "to",
}

# Kinds that echo back to the player who authored them (via
# payload.author_player_id). GM is implicit recipient. Non-author,
# non-GM players do not see these.
SELF_AUTHORED_KINDS: frozenset[str] = frozenset(
    {
        "PLAYER_ACTION",
        "DICE_THROW",
        "BEAT_SELECTION",
        "CHARACTER_CREATION",
    }
)

# Kinds never routed to non-GM players. GM gets them via the GM invariant.
GM_ONLY_KINDS: frozenset[str] = frozenset({"THINKING"})


@dataclass(frozen=True)
class InvariantOutcome:
    """Result of running CoreInvariantStage on one envelope.

    ``terminal=True`` means a core invariant decided the outcome — the
    ``decision`` and ``source`` fields are both populated and the
    GenreRuleStage must not run. ``source`` is one of the
    ``invariant:*`` strings emitted as the OTEL ``rule.source`` attribute.

    ``terminal=False`` means no invariant applied; ``decision`` and
    ``source`` are both ``None`` and GenreRuleStage should be consulted.

    The stage itself owns the source string (I7): it is the only place
    that knows *which* invariant fired. The composed filter does not
    redundantly re-check the envelope kind — that avoided the previous
    silent ``"invariant:unknown"`` fallback when invariants drift.
    """

    terminal: bool
    decision: FilterDecision | None
    source: str | None = None


class CoreInvariantStage:
    """Hardcoded structural filters. No configuration."""

    def evaluate(
        self,
        *,
        envelope: MessageEnvelope,
        view: GameStateView,
        player_id: str,
    ) -> InvariantOutcome:
        # 1. GM sees canonical — always.
        if view.is_gm(player_id):
            return InvariantOutcome(
                terminal=True,
                decision=FilterDecision(include=True, payload_json=envelope.payload_json),
                source="invariant:gm_sees_all",
            )

        # 2. Targeted-by-field: kinds that declare a recipient in their payload.
        if envelope.kind in TARGETED_KINDS:
            field_name = TARGETED_KINDS[envelope.kind]
            payload = json.loads(envelope.payload_json)
            to_value = payload.get(field_name)
            included = _match_to_field(to_value, player_id)
            return InvariantOutcome(
                terminal=True,
                decision=FilterDecision(
                    include=included,
                    payload_json=envelope.payload_json if included else "",
                ),
                source="invariant:targeted",
            )

        # 3. Self-authored: echo to author + GM only.
        if envelope.kind in SELF_AUTHORED_KINDS:
            payload = json.loads(envelope.payload_json)
            author = payload.get("author_player_id")
            included = isinstance(author, str) and author == player_id
            return InvariantOutcome(
                terminal=True,
                decision=FilterDecision(
                    include=included,
                    payload_json=envelope.payload_json if included else "",
                ),
                source="invariant:self_echo",
            )

        # 4. GM-only kinds: never route to players.
        if envelope.kind in GM_ONLY_KINDS:
            return InvariantOutcome(
                terminal=True,
                decision=FilterDecision(include=False, payload_json=""),
                source="invariant:gm_only_kind",
            )

        return InvariantOutcome(terminal=False, decision=None, source=None)


def _match_to_field(to_value: object, player_id: str) -> bool:
    """Return True if player_id is named by a `to` field (scalar or list)."""
    if isinstance(to_value, str):
        return to_value == player_id
    if isinstance(to_value, list):
        return player_id in to_value
    return False
