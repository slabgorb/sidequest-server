"""Story 2026-05-10 — taunt round-advance tick.

Called once per round-advance from ``_execute_narration_turn`` in
``sidequest/server/websocket_session_handler.py``, right after
``snapshot.turn_manager.record_interaction()``.

Responsibilities:
1. Run ``TauntState.end_of_round_decay()`` on the encounter's taunt tracker.
2. When the taunter just expired (active_actor was non-None and is now None),
   emit ``encounter.taunt.expired`` OTEL span with ``actor_id`` (the prior
   taunter) and ``round`` (the round that just ended, i.e., ``prior_round``).

Spec: docs/superpowers/specs/2026-05-10-class-mechanical-surface-design.md §8.

Wire site comment:
  # Story 2026-05-10 — taunt decay tick (Task 6).
  # Runs on every round-advance so the 1-round duration is enforced
  # mechanically, not left to narrator improvisation.
  if snapshot.encounter is not None and not snapshot.encounter.resolved:
      from sidequest.game.taunt_tick import tick_taunt_round_advance
      tick_taunt_round_advance(
          snapshot.encounter,
          prior_round=prior_round,
      )
"""

from __future__ import annotations

from sidequest.game.encounter import StructuredEncounter
from sidequest.telemetry.spans import SPAN_ENCOUNTER_TAUNT_EXPIRED
from sidequest.telemetry.spans.span import Span
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish


def tick_taunt_round_advance(enc: StructuredEncounter, *, prior_round: int) -> None:
    """Decay the encounter's taunt by one round and emit expiry OTEL if it lapsed.

    Args:
        enc:         The active ``StructuredEncounter`` whose taunt to decay.
        prior_round: The round counter value *before* the increment —
                     i.e., the round that just ended.  Stored on the expiry
                     span so the GM panel can correlate with the interaction log.
    """
    prior_actor = enc.taunt.active_actor
    enc.taunt.end_of_round_decay()

    # Emit expiry only when the taunt was active and the decay just cleared it.
    if prior_actor is not None and enc.taunt.active_actor is None:
        with Span.open(
            SPAN_ENCOUNTER_TAUNT_EXPIRED,
            {
                "actor_id": prior_actor,
                "round": prior_round,
            },
        ):
            pass
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter.taunt",
                "op": "expired",
                "actor_id": prior_actor,
                "round": prior_round,
            },
            component="encounter",
        )
