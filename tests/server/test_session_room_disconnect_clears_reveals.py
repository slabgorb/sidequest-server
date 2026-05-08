"""Verify SessionRoom.disconnect emits ACTION_REVEAL cleared."""

from sidequest.game.persistence import GameMode
from sidequest.protocol.messages import ActionRevealMessage, ActionRevealStatus
from sidequest.server.session_room import SessionRoom


def _make_room_with_player(
    slug: str = "test",
    player_id: str = "p1",
    socket_id: str = "s1",
) -> SessionRoom:
    """Construct a SessionRoom with one connected player (no seat)."""
    room = SessionRoom(slug=slug, mode=GameMode.MULTIPLAYER)
    room._sockets[socket_id] = player_id
    room._connected[player_id] = socket_id
    return room


def test_disconnect_emits_cleared_for_departed_player() -> None:
    room = _make_room_with_player()
    captured: list[tuple[object, dict]] = []

    def fake_broadcast(msg, *, exclude_socket_id=None):
        captured.append((msg, {"exclude_socket_id": exclude_socket_id}))
        return []

    room.broadcast = fake_broadcast  # type: ignore[assignment]

    result = room.disconnect(socket_id="s1")

    assert result == "p1"
    cleared = [m for m, _ in captured if isinstance(m, ActionRevealMessage)]
    assert len(cleared) == 1, f"expected exactly one cleared broadcast, got {len(cleared)}"
    assert cleared[0].payload.status == ActionRevealStatus.CLEARED
    assert str(cleared[0].payload.player_id) == "p1"
    assert cleared[0].payload.action == ""


def test_disconnect_emits_cleared_with_exclude_none() -> None:
    """Cleared broadcasts go to everyone — including any reconnect-phase
    socket of the same player — so all peers' rows flush."""
    room = _make_room_with_player()
    captured: list[tuple[object, dict]] = []

    def fake_broadcast(msg, *, exclude_socket_id=None):
        captured.append((msg, {"exclude_socket_id": exclude_socket_id}))
        return []

    room.broadcast = fake_broadcast  # type: ignore[assignment]
    room.disconnect(socket_id="s1")

    cleared = [(m, kw) for m, kw in captured if isinstance(m, ActionRevealMessage)]
    assert cleared[0][1]["exclude_socket_id"] is None


def test_disconnect_no_player_no_cleared_emitted() -> None:
    """If the socket isn't tracked, no cleared broadcast fires."""
    room = SessionRoom(slug="test", mode=GameMode.MULTIPLAYER)
    captured: list[object] = []

    def fake_broadcast(msg, *, exclude_socket_id=None):
        captured.append(msg)
        return []

    room.broadcast = fake_broadcast  # type: ignore[assignment]
    result = room.disconnect(socket_id="unknown-socket")

    assert result is None
    assert not any(isinstance(m, ActionRevealMessage) for m in captured)
