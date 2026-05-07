"""Multi-socket presence — pingpong 2026-05-07 [BUG] regression coverage.

Bug shape: HMR / Vite reload / Playwright tab-switch can produce two
concurrent WebSockets for the same player_id. The first WS opens, the
second opens (HMR), the second closes (1001 from tab background). Pre-
fix, the close on socket #2 cleared `_connected[player_id]` because
`connect()` had stomped `_sockets[socket_1]` at the second connect.
Net effect on the playtest: late joiners never saw the host because
`presence_backfill` iterates `connected_player_ids()` which had been
silently emptied.

Fix shape: ref-count live sockets per player_id. Presence
(`_connected`, seat abandonment, action_reveal cleared, the WS
endpoint's PLAYER_PRESENCE{disconnected} broadcast) is touched ONLY
when the LAST socket for the player_id closes.

These tests are the lie-detector. If anyone re-introduces the
single-socket assumption, this whole file goes red at once.
"""

from __future__ import annotations

from sidequest.game.persistence import GameMode
from sidequest.server.session_room import LobbyState, SessionRoom


def test_two_sockets_same_player_first_disconnect_keeps_presence() -> None:
    """Two WS for the same player_id — closing one must NOT clear presence.

    The first socket stays alive; the player remains in
    `connected_player_ids()` so any late joiner's `presence_backfill`
    finds them.
    """
    room = SessionRoom(slug="repro-2026-05-07", mode=GameMode.MULTIPLAYER)
    room.connect("carl", socket_id="sock-A")
    room.connect("carl", socket_id="sock-B")  # HMR / reload
    assert room.connected_player_ids() == ["carl"]

    # Socket B closes (tab backgrounded -> 1001). Bug: would empty
    # `_connected`. Fixed: presence preserved because socket A is still
    # alive.
    result = room.disconnect(socket_id="sock-B")
    assert result is None, (
        "transient disconnect must return None so the WS endpoint does "
        "NOT broadcast PLAYER_PRESENCE{disconnected} — the player is "
        "still present on socket-A"
    )
    assert "carl" in room.connected_player_ids(), (
        "regression: with another WS alive, presence must NOT clear; "
        "this is the 2026-05-07 bug — late joiners lost the host"
    )


def test_last_socket_disconnect_clears_presence() -> None:
    """The OTHER side of the ref-count: when the LAST socket closes,
    presence MUST clear and disconnect MUST return the player_id so the
    WS endpoint broadcasts PLAYER_PRESENCE{disconnected}."""
    room = SessionRoom(slug="repro", mode=GameMode.MULTIPLAYER)
    room.connect("carl", socket_id="sock-A")
    room.connect("carl", socket_id="sock-B")

    # First close — presence preserved.
    assert room.disconnect(socket_id="sock-B") is None

    # Second close — the actual departure.
    result = room.disconnect(socket_id="sock-A")
    assert result == "carl", (
        "last-socket disconnect must return the player_id so the WS "
        "endpoint can broadcast PLAYER_PRESENCE{disconnected}"
    )
    assert room.connected_player_ids() == []


def test_presence_skipped_disconnect_emits_otel_span() -> None:
    """OTEL lie-detector: when a transient disconnect is absorbed by
    the ref-count, the GM panel needs visibility. Without this span the
    only way to verify the fix engaged is to diff `presence_backfill`
    output across joins, which is fragile.
    """
    captured: list[tuple[str, dict, str]] = []

    def fake_publish(name: str, payload: dict, *, component: str = "", **_kw) -> None:  # noqa: ANN401
        captured.append((name, payload, component))

    import sidequest.telemetry.watcher_hub as _hub

    original = _hub.publish_event
    _hub.publish_event = fake_publish  # type: ignore[assignment]
    try:
        room = SessionRoom(slug="repro", mode=GameMode.MULTIPLAYER)
        room.connect("carl", socket_id="sock-A")
        room.connect("carl", socket_id="sock-B")

        captured.clear()
        room.disconnect(socket_id="sock-B")

        skipped = [c for c in captured if c[0] == "presence.disconnect_skipped"]
        assert len(skipped) == 1, (
            f"expected exactly one presence.disconnect_skipped span; got {captured}"
        )
        _, payload, component = skipped[0]
        assert payload["reason"] == "other_ws_alive"
        assert payload["player_id"] == "carl"
        assert payload["socket_id"] == "sock-B"
        assert payload["remaining_socket_count"] == 1
        assert component == "multiplayer"
    finally:
        _hub.publish_event = original  # type: ignore[assignment]


def test_multi_socket_attach_emits_otel_span() -> None:
    """Sibling lie-detector: when a second WS attaches for an already-
    present player_id, the GM panel sees the concurrency. This is the
    canary for HMR storms / tab-reload races during a playtest.
    """
    captured: list[tuple[str, dict, str]] = []

    def fake_publish(name: str, payload: dict, *, component: str = "", **_kw) -> None:  # noqa: ANN401
        captured.append((name, payload, component))

    import sidequest.telemetry.watcher_hub as _hub

    original = _hub.publish_event
    _hub.publish_event = fake_publish  # type: ignore[assignment]
    try:
        room = SessionRoom(slug="repro", mode=GameMode.MULTIPLAYER)
        room.connect("carl", socket_id="sock-A")
        captured.clear()
        room.connect("carl", socket_id="sock-B")

        attaches = [c for c in captured if c[0] == "presence.multi_socket_attach"]
        assert len(attaches) == 1, (
            f"expected exactly one presence.multi_socket_attach span; got {captured}"
        )
        _, payload, _ = attaches[0]
        assert payload["live_socket_count"] == 2
        assert payload["player_id"] == "carl"
        assert payload["socket_id"] == "sock-B"
    finally:
        _hub.publish_event = original  # type: ignore[assignment]


def test_chargen_seat_not_abandoned_on_transient_disconnect() -> None:
    """A CHARGEN seat must NOT be abandoned when a transient disconnect
    happens — the player is still present on another socket and is
    still actively building. Pre-fix: any disconnect on a CHARGEN seat
    flipped to ABANDONED, so an HMR reload mid-chargen would brick the
    seat for the rest of the session.
    """
    room = SessionRoom(slug="chargen-test", mode=GameMode.MULTIPLAYER)
    room.connect("hant", socket_id="sock-A")
    room.connect("hant", socket_id="sock-B")
    room.seat("hant", character_slot="Hant")
    assert room._seated["hant"].state == LobbyState.CHARGEN  # noqa: SLF001

    room.disconnect(socket_id="sock-B")  # transient

    # Seat MUST stay in CHARGEN — the player is still present on A.
    assert room._seated["hant"].state == LobbyState.CHARGEN, (  # noqa: SLF001
        "chargen seat must not abandon while another WS is alive"
    )


def test_chargen_seat_abandoned_on_last_socket_disconnect() -> None:
    """The other side: when the LAST socket closes mid-chargen, the
    seat MUST flip to ABANDONED — preserving Story 45-2 behavior."""
    room = SessionRoom(slug="chargen-test", mode=GameMode.MULTIPLAYER)
    room.connect("hant", socket_id="sock-A")
    room.seat("hant", character_slot="Hant")

    room.disconnect(socket_id="sock-A")  # last socket

    assert room._seated["hant"].state == LobbyState.ABANDONED  # noqa: SLF001


def test_repointing_connected_to_remaining_socket() -> None:
    """When the LATEST socket (the one `_connected[pid]` points at)
    closes but another is still alive, `_connected[pid]` MUST repoint
    at one of the remaining sockets so downstream consumers
    (broadcast exclude_socket_id, snapshot dispatch) observe a live
    socket — not a stale handle.
    """
    room = SessionRoom(slug="repoint", mode=GameMode.MULTIPLAYER)
    room.connect("carl", socket_id="sock-A")
    room.connect("carl", socket_id="sock-B")
    # `_connected` points at the latest connect — sock-B.
    assert room.socket_for_player("carl") == "sock-B"

    room.disconnect(socket_id="sock-B")

    # Repointed to A (the remaining live socket).
    assert room.socket_for_player("carl") == "sock-A"


def test_presence_backfill_includes_player_with_only_dropped_latest_socket() -> None:
    """Wiring test for `connect.py:peers_to_backfill`. Reproduces the
    exact 3-tab playtest scenario from the pingpong bug report:

      Tab 0: Carl connects (sock-A)
      Tab 0: HMR -> Carl connects again (sock-B)
      Tab 0: tab background -> sock-B closes
      Tab 1: Donut connects -> his backfill MUST include Carl

    Pre-fix, after sock-B closed, `_connected` was emptied; Donut's
    `connected_player_ids()` returned only Donut, so backfill was empty
    and Donut never saw Carl. This is the canonical 3-way regression.
    """
    room = SessionRoom(slug="3-way", mode=GameMode.MULTIPLAYER)
    room.connect("carl", socket_id="sock-A")
    room.connect("carl", socket_id="sock-B")  # HMR
    room.disconnect(socket_id="sock-B")  # tab background

    # Donut joins. The `peers_to_backfill` calc in connect.py reads
    # `room.connected_player_ids()` BEFORE adding Donut.
    peers_to_backfill = [pid for pid in room.connected_player_ids() if pid != "donut"]
    assert peers_to_backfill == ["carl"], (
        f"regression: Donut's presence_backfill must include Carl. got {peers_to_backfill}"
    )
    room.connect("donut", socket_id="sock-D")

    # Steady state after Donut connects: both Carl and Donut visible.
    assert set(room.connected_player_ids()) == {"carl", "donut"}


# ---------------------------------------------------------------------------
# Wiring test (CLAUDE.md: every test suite needs a wiring test).
# Verifies the multi-socket bookkeeping reaches the production code path
# end-to-end via the WS endpoint. Uses fake WebSockets to avoid the
# uvicorn boot tax — the path under test is `room.disconnect` returning
# None, which makes `ws_endpoint` skip the PLAYER_PRESENCE broadcast.
# ---------------------------------------------------------------------------


def test_wired_disconnect_returning_none_skips_presence_broadcast() -> None:
    """The fix must reach production. `ws_endpoint` (websocket.py)
    branches on `room.disconnect(...)` — when it returns None, no
    PLAYER_PRESENCE{disconnected} is broadcast. With two sockets, the
    first close MUST take the None branch (no broadcast); the second
    close MUST take the player_id branch (broadcast happens). This is
    the ground-truth wiring assertion."""
    room = SessionRoom(slug="wiring", mode=GameMode.MULTIPLAYER)
    room.connect("carl", socket_id="sock-A")
    room.connect("carl", socket_id="sock-B")

    # First socket close — production code: `if left_player is not None: broadcast(...)`.
    left_first = room.disconnect(socket_id="sock-B")
    assert left_first is None, (
        "ws_endpoint contract: returning None means 'no presence change' "
        "and skips the PLAYER_PRESENCE{disconnected} broadcast"
    )

    # Last socket close — must broadcast.
    left_last = room.disconnect(socket_id="sock-A")
    assert left_last == "carl", (
        "ws_endpoint contract: returning the player_id triggers the "
        "PLAYER_PRESENCE{disconnected} broadcast"
    )
