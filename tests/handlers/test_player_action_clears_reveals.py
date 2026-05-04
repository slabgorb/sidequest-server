"""Verify dispatch fires ACTION_REVEAL cleared for every party member."""

from unittest.mock import MagicMock

from sidequest.handlers.player_action import _broadcast_cleared_to_party
from sidequest.protocol.messages import (
    ActionRevealMessage,
    ActionRevealStatus,
)


def test_broadcast_cleared_to_party_emits_one_per_member() -> None:
    room = MagicMock()
    broadcast_calls: list[tuple[object, dict]] = []
    room.broadcast.side_effect = (
        lambda msg, **kw: broadcast_calls.append((msg, kw)) or []
    )
    party_members = [
        {"player_id": "p1", "character_name": "Alex"},
        {"player_id": "p2", "character_name": "Bob"},
        {"player_id": "p3", "character_name": "Carol"},
    ]

    _broadcast_cleared_to_party(
        room, party_members, round_no=7, reason="dispatch"
    )

    assert room.broadcast.call_count == 3
    statuses = [m.payload.status for m, _ in broadcast_calls]
    player_ids = [m.payload.player_id for m, _ in broadcast_calls]
    rounds = [m.payload.round for m, _ in broadcast_calls]
    assert all(s == ActionRevealStatus.CLEARED for s in statuses)
    assert [str(pid) for pid in player_ids] == ["p1", "p2", "p3"]
    assert all(r == 7 for r in rounds)
    # exclude_socket_id=None — cleared goes to everyone, even the
    # last-submitter, who needs their own row to clear.
    assert all(kw.get("exclude_socket_id") is None for _, kw in broadcast_calls)


def test_broadcast_cleared_uses_action_reveal_message() -> None:
    """Verify each broadcast wraps an ActionRevealMessage (not a raw dict)."""
    room = MagicMock()
    broadcast_calls: list[tuple[object, dict]] = []
    room.broadcast.side_effect = (
        lambda msg, **kw: broadcast_calls.append((msg, kw)) or []
    )
    party_members = [{"player_id": "p1", "character_name": "Alex"}]

    _broadcast_cleared_to_party(
        room, party_members, round_no=3, reason="dispatch"
    )

    assert isinstance(broadcast_calls[0][0], ActionRevealMessage)
    assert broadcast_calls[0][0].payload.action == ""
    assert broadcast_calls[0][0].payload.aside is False
    assert broadcast_calls[0][0].payload.seq == 0


def test_broadcast_cleared_empty_party_is_a_noop() -> None:
    room = MagicMock()
    _broadcast_cleared_to_party(
        room, [], round_no=7, reason="dispatch"
    )
    room.broadcast.assert_not_called()
