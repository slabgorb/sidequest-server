"""YIELD dispatch handler — structured player exit.

Spec 2026-04-25-dual-track-momentum-design.md §Yield action. The yielding
actor is marked ``withdrawn``; the encounter resolves when every
``side="player"`` actor has yielded or been taken out. Edge is refunded by
``1 + count_of_scratch-or-worse-statuses-created-this-encounter``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sidequest.game.resolution_signal import ResolutionSignal
from sidequest.game.session import GameSnapshot
from sidequest.game.status import Status
from sidequest.telemetry.spans import (
    encounter_resolution_signal_emitted_span,
    encounter_yield_received_span,
    encounter_yield_resolved_span,
)
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

if TYPE_CHECKING:
    from sidequest.server.session_room import SessionRoom


def _statuses_taken_in_encounter(
    statuses: list[Status],
    encounter_type: str,
) -> int:
    return sum(1 for s in statuses if s.created_in_encounter == encounter_type)


def _refund_edge_for_yielders(
    snapshot: GameSnapshot,
    yielded_names: list[str],
    encounter_type: str,
) -> int:
    total_refund = 0
    for name in yielded_names:
        char = next((c for c in snapshot.characters if c.core.name == name), None)
        if char is None:
            continue
        count = _statuses_taken_in_encounter(char.core.statuses, encounter_type)
        refund = 1 + count
        before = char.core.edge.current
        char.core.apply_edge_delta(refund)
        total_refund += char.core.edge.current - before
    return total_refund


def handle_yield(
    snapshot: GameSnapshot,
    *,
    room: SessionRoom,
    player_id: str,
    player_name: str,
) -> None:
    enc = snapshot.encounter
    if enc is None or enc.resolved:
        raise ValueError("YIELD: no active encounter")
    actor = enc.find_actor_for_player(player_name)
    if actor is None:
        raise ValueError(f"YIELD: no player-side actor named {player_name!r}")
    if actor.withdrawn:
        return  # idempotent

    actor_char = next(
        (c for c in snapshot.characters if c.core.name == player_name),
        None,
    )
    statuses_taken = (
        _statuses_taken_in_encounter(actor_char.core.statuses, enc.encounter_type)
        if actor_char
        else 0
    )
    with encounter_yield_received_span(
        player_id=player_id,
        actor_name=player_name,
        prior_player_metric=enc.player_metric.current,
        prior_opponent_metric=enc.opponent_metric.current,
        statuses_taken_this_encounter=statuses_taken,
    ):
        pass
    _watcher_publish(
        "state_transition",
        {
            "field": "encounter",
            "op": "yield_received",
            "encounter_type": enc.encounter_type,
            "player_id": player_id,
            "actor_name": player_name,
            "prior_player_metric": enc.player_metric.current,
            "prior_opponent_metric": enc.opponent_metric.current,
            "statuses_taken_this_encounter": statuses_taken,
        },
        component="encounter",
    )

    actor.withdrawn = True

    player_actors = [a for a in enc.actors if a.side == "player"]
    all_done = all(a.withdrawn for a in player_actors)
    if not all_done:
        return  # encounter remains active

    yielded_names = [a.name for a in player_actors if a.withdrawn]
    edge_refreshed = _refund_edge_for_yielders(
        snapshot,
        yielded_names,
        enc.encounter_type,
    )

    enc.resolved = True
    enc.outcome = "yielded"

    # Yielded out of the encounter — scene ended for the party. Sweep
    # Scratch (Playtest 2026-04-26 Bug #1). Wound/Scar persist; this is
    # the same trigger as narrator-beat / dice-beat resolution.
    room.session.end_scene(
        "scene_end",
        turn=snapshot.turn_manager.interaction,
    )

    snapshot.pending_resolution_signal = ResolutionSignal(
        encounter_type=enc.encounter_type,
        outcome="yielded",
        final_player_metric=enc.player_metric.current,
        final_opponent_metric=enc.opponent_metric.current,
        yielded_actors=tuple(yielded_names),
        edge_refreshed=edge_refreshed,
    )

    with encounter_yield_resolved_span(
        outcome="yielded",
        yielded_actors=tuple(yielded_names),
        edge_refreshed=edge_refreshed,
    ):
        pass
    _watcher_publish(
        "state_transition",
        {
            "field": "encounter",
            "op": "yield_resolved",
            "encounter_type": enc.encounter_type,
            "outcome": "yielded",
            "yielded_actors": list(yielded_names),
            "edge_refreshed": edge_refreshed,
        },
        component="encounter",
    )
    with encounter_resolution_signal_emitted_span(
        outcome="yielded",
        final_player_metric=enc.player_metric.current,
        final_opponent_metric=enc.opponent_metric.current,
    ):
        pass
    _watcher_publish(
        "state_transition",
        {
            "field": "encounter",
            "op": "resolved",
            "encounter_type": enc.encounter_type,
            "outcome": "yielded",
            "source": "yield",
            "final_player_metric": enc.player_metric.current,
            "final_opponent_metric": enc.opponent_metric.current,
        },
        component="encounter",
    )
