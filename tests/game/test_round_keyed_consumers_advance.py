"""Regression-lock: round-keyed gating consumers must read the now-advancing
``turn_manager.round`` after Story 45-11's lockstep fix lands.

Three production consumers read ``snapshot.turn_manager.round`` directly
(verified by grep, 2026-04-28):

1. ``sidequest/handlers/player_action.py:218`` — ``mp.barrier_fired``
   watcher event payload.
2. ``sidequest/handlers/player_action.py:246`` — ``mp.round_dispatched``
   watcher event payload.
3. ``sidequest/game/world_materialization.py:95`` —
   ``CampaignMaturity.from_snapshot`` derives the maturity bucket
   (Fresh / Early / Mid / Veteran) from ``turn_manager.round + beats/2``.

Before 45-11 GREEN, ``turn_manager.round`` is frozen at its initial value
(usually 1) and these consumers see stale data: ``mp.barrier_fired`` reports
``round=1`` indefinitely, the GM panel chart flatlines, and
``CampaignMaturity`` stays ``Fresh`` for the entire campaign — even at
turn 72 (Felix's session). After GREEN, ``record_interaction`` advances
both counters in lockstep and these consumers see the same monotonic
counter ``MAX(narrative_log.round_number)`` reports.

Why ``CampaignMaturity`` was chosen as the lock target:

- It is a pure function over ``snapshot`` — testable without a SessionRoom.
- The bucket boundaries (5 / 20 / 50) are concrete observable assertions:
  ``turn_manager.round=21`` MUST land in ``Mid``, not ``Fresh``.
- It is downstream of every Lane B story that gates on round/turn — fixing
  the maturity gate by accident is exactly the kind of regression that's
  invisible without a lock test.

The mp.barrier_fired and mp.round_dispatched payloads are exercised by the
boundary test in ``tests/server/test_turn_manager_round_invariant.py``
(post-fix the snapshot value matches MAX(round_number); the watcher event
reads the same field).

This test is RED until 45-11 GREEN wires the lockstep advance into
``record_interaction``.
"""

from __future__ import annotations

from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.game.world_materialization import CampaignMaturity


def test_campaign_maturity_advances_after_record_interaction_ticks() -> None:
    """``CampaignMaturity.from_snapshot`` MUST escape ``Fresh`` once enough
    interactions have run.

    Today (RED): ``record_interaction`` only advances ``interaction``;
    ``turn_manager.round`` stays at 1; ``effective = 1 + 0/2 = 1``;
    bucket = ``Fresh`` forever. A 72-turn Felix session is still Fresh.

    After 45-11 GREEN: ``record_interaction`` advances ``round`` in
    lockstep. After 6 ticks ``round=7``, ``effective=7``, bucket = ``Early``.
    """
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    # Snapshot starts with TurnManager(round=1, interaction=1).
    assert CampaignMaturity.from_snapshot(snap) == CampaignMaturity.Fresh

    # Drive 6 interactions through the production seam (record_interaction).
    for _ in range(6):
        snap.turn_manager.record_interaction()

    # AC2 / Strategy A — round must track interaction. effective = 7.
    # Per CampaignMaturity buckets (effective <= 5 = Fresh, <= 20 = Early):
    # 7 falls in Early. The current bug stays in Fresh because
    # turn_manager.round is frozen at 1.
    assert CampaignMaturity.from_snapshot(snap) == CampaignMaturity.Early, (
        f"after 6 record_interaction calls, expected Early bucket; "
        f"got {CampaignMaturity.from_snapshot(snap)} "
        f"(turn_manager.round={snap.turn_manager.round}, "
        f"interaction={snap.turn_manager.interaction}). This is the "
        f"Felix divergence reaching CampaignMaturity — round-keyed "
        f"gating reads a stale 1 forever."
    )


def test_campaign_maturity_reaches_veteran_after_long_session() -> None:
    """A 60-interaction session must register as ``Veteran`` (effective>50),
    not stay in ``Fresh`` because ``round`` never advanced.

    This is the Felix shape distilled — his 72-interaction save SHOULD have
    been Veteran from the narrator's prompt-context perspective, but the
    maturity reader was looking at frozen round=1 and saying ``Fresh``.
    Round-keyed gating became "everyone is always at the start of the
    campaign," which is exactly the stale-data class the story closes.
    """
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    for _ in range(60):
        snap.turn_manager.record_interaction()

    # round=61, beats=0, effective=61 → Veteran.
    assert CampaignMaturity.from_snapshot(snap) == CampaignMaturity.Veteran, (
        f"60-interaction session should register Veteran; "
        f"got {CampaignMaturity.from_snapshot(snap)} "
        f"(round={snap.turn_manager.round}). Felix's 72-turn save was "
        f"reading Fresh forever — that's the bug this story closes."
    )


def test_turn_manager_round_initial_state_unchanged() -> None:
    """Defensive: a brand-new TurnManager still reports round=1, interaction=1.

    The lockstep fix changes the *advance* behavior, not initial state.
    If GREEN accidentally bumps the default seed for round (e.g. =0 to
    avoid an off-by-one in the gap calculation), this lock catches it
    before any save-file migration breaks.
    """
    tm = TurnManager()
    assert tm.round == 1
    assert tm.interaction == 1