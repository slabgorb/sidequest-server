"""Lobby state machine tests for Story 45-2 (RED phase).

Story 45-2 (wire-first): the structured-mode turn barrier today counts every
seated peer, including peers still in chargen, so a solo player can hit
barriers waiting on phantom lobby connections (Playtest 3 evropi, 2026-04-19).
The fix is an explicit lobby state machine on `_Seat`:

    CONNECTED → CLAIMING_SEAT → CHARGEN → PLAYING
                                  └────► ABANDONED   (chargen-mid disconnect)
                                  PLAYING (paused — keeps slot held)

This file covers the unit-level state-machine surface area: the enum, the
state field on `_Seat`, the `playing_player_ids()` / `playing_player_count()`
predicates, the `chargen → abandoned` transition on disconnect, the
preserved `is_paused()` semantics for `playing`-but-disconnected peers, and
the OTEL spans that must fire on transitions.

Wire-first boundary tests (the actual barrier-decision seam) live in
`tests/server/test_mp_turn_barrier_active_turn_count.py`.

These tests are RED today: `LobbyState` does not exist; `_Seat` has no
`state` field; `playing_player_ids()` is unimplemented; `disconnect()` does
not transition CHARGEN → ABANDONED; no lobby-state OTEL spans are emitted.

See `sprint/context/context-story-45-2.md` for the full design.
"""
from __future__ import annotations

import sidequest.telemetry.watcher_hub as _hub
from sidequest.game.persistence import GameMode
from sidequest.server.session_room import SessionRoom

# ---------------------------------------------------------------------------
# Enum + new predicates (AC2 surface)
# ---------------------------------------------------------------------------


def test_lobby_state_enum_has_five_named_values() -> None:
    """The `LobbyState` enum is the contract for the state machine.

    Five states per spec:
      CONNECTED, CLAIMING_SEAT, CHARGEN, PLAYING, ABANDONED.

    RED today: import fails — `LobbyState` is not defined in session_room.
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    expected = {"CONNECTED", "CLAIMING_SEAT", "CHARGEN", "PLAYING", "ABANDONED"}
    actual = {member.name for member in LobbyState}
    assert actual == expected, (
        f"LobbyState must have exactly these states; got {actual} vs {expected}"
    )


def test_room_exposes_playing_player_ids_and_count() -> None:
    """`SessionRoom.playing_player_ids()` and `playing_player_count()` are
    the new sibling predicates that filter on `state == PLAYING` (spec).

    They are the predicate the turn barrier will read instead of
    `seated_player_count()`. RED today: methods do not exist.
    """
    room = SessionRoom(slug="test", mode=GameMode.MULTIPLAYER)
    # Method existence must be attribute access on the instance — the spec
    # adds them on SessionRoom, not on a sibling helper class.
    assert callable(getattr(room, "playing_player_ids", None)), (
        "SessionRoom.playing_player_ids() must exist"
    )
    assert callable(getattr(room, "playing_player_count", None)), (
        "SessionRoom.playing_player_count() must exist"
    )
    # Empty room: zero playing players. This must hold *before* any peer
    # connects so the predicate is well-defined at session-start.
    assert room.playing_player_ids() == []
    assert room.playing_player_count() == 0


# ---------------------------------------------------------------------------
# Seat state field (AC2)
# ---------------------------------------------------------------------------


def test_seat_after_player_seat_starts_in_chargen_not_playing() -> None:
    """After PLAYER_SEAT (i.e. `room.seat()`), the peer is in CHARGEN — NOT
    PLAYING. This is the heart of the fix: a peer in chargen should not be
    counted by the turn barrier.

    RED today: `_Seat` has no state field; `seat()` only writes
    `(player_id, character_slot)`. There is no separation between "in
    chargen" and "playing".
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    room = SessionRoom(slug="evropi-fixture", mode=GameMode.MULTIPLAYER)
    room.connect("rux", socket_id="sock-rux")
    room.seat("rux", character_slot="Rux")

    # The internal _seated dict is the durable seat record. `_Seat` must
    # carry an explicit state so callers can ask "is this peer playing?".
    seat = room._seated["rux"]  # noqa: SLF001 — testing private structure by design
    assert getattr(seat, "state", None) == LobbyState.CHARGEN, (
        "seat() must transition to CHARGEN (not directly to PLAYING) — "
        "chargen happens AFTER seat-claim"
    )

    # Predicate-level corollary: a chargen peer is NOT counted as playing.
    assert "rux" not in room.playing_player_ids()
    assert room.playing_player_count() == 0
    # But they ARE seated (existing predicates unchanged, per spec).
    assert "rux" in room.seated_player_ids()
    assert room.seated_player_count() == 1


# ---------------------------------------------------------------------------
# Chargen-abandonment transition (AC3)
# ---------------------------------------------------------------------------


def test_disconnect_during_chargen_marks_seat_abandoned() -> None:
    """The killer transition: when a peer disconnects WHILE in CHARGEN,
    their seat must move to ABANDONED so the barrier stops waiting on it.

    This is the evropi scenario at the unit level — three peers seated
    but never reached `_chargen_confirmation`, then the WS dropped.

    RED today: `disconnect()` removes the socket but never touches `_seated`.
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    room = SessionRoom(slug="evropi-fixture", mode=GameMode.MULTIPLAYER)
    room.connect("hant", socket_id="sock-hant")
    room.seat("hant", character_slot="Hant")
    # In chargen — has not committed yet.
    assert room._seated["hant"].state == LobbyState.CHARGEN  # noqa: SLF001

    # WS drops mid-chargen.
    returned_pid = room.disconnect(socket_id="sock-hant")
    assert returned_pid == "hant"

    # Seat must transition to ABANDONED — the spec's #4 dimension. The seat
    # record stays (so `seated_player_ids()` still surfaces the slot for
    # GM-panel forensics) but it is NOT counted as playing.
    seat = room._seated["hant"]  # noqa: SLF001
    assert seat.state == LobbyState.ABANDONED, (
        "chargen-abandonment must transition the seat to ABANDONED, not leave "
        "it dangling in CHARGEN"
    )
    assert "hant" not in room.playing_player_ids()
    assert room.playing_player_count() == 0


def test_disconnect_while_playing_keeps_seat_in_playing() -> None:
    """The other side of the spec: a `playing` peer disconnecting must NOT
    abandon their seat. Their character is in the world; the slot stays
    held (existing pause semantics — `is_paused()` returns True).

    Negative test from AC3 — distinguishes correctness from "always
    abandon on disconnect".

    RED today: no PLAYING state to preserve, no transition guard.
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    room = SessionRoom(slug="evropi-fixture", mode=GameMode.MULTIPLAYER)
    room.connect("rux", socket_id="sock-rux")
    room.seat("rux", character_slot="Rux")
    # Test fixture: simulate "chargen completed". The exact API for this
    # transition is Dev's choice (could be `room.mark_playing()`, could be
    # state mutation via `_chargen_confirmation`). Either way, the
    # `playing` state is reachable on the seat.
    room._seated["rux"].state = LobbyState.PLAYING  # noqa: SLF001

    # Now WS drops while peer is PLAYING.
    returned_pid = room.disconnect(socket_id="sock-rux")
    assert returned_pid == "rux"

    # Seat must STAY in PLAYING (the slot is held; pause kicks in).
    seat = room._seated["rux"]  # noqa: SLF001
    assert seat.state == LobbyState.PLAYING, (
        "playing-but-disconnected must NOT abandon the seat; pause semantics "
        "depend on the seat persisting"
    )
    # Predicate: still counted as playing (the barrier still waits on
    # them — the game is paused, not abandoned).
    assert "rux" in room.playing_player_ids()
    assert room.playing_player_count() == 1


# ---------------------------------------------------------------------------
# Existing pause semantics preserved (AC6 regression)
# ---------------------------------------------------------------------------


def test_is_paused_still_true_for_disconnected_playing_peer() -> None:
    """REGRESSION (AC6): the new state machine must be ADDITIVE. A
    playing-but-disconnected peer must continue to:
      - appear in `absent_seated_player_ids()`
      - cause `is_paused()` to return True

    The pause-banner UI reads `is_paused()`; breaking it would force a
    UI scope-creep into this story.

    RED today on the chain: the scenario depends on the PLAYING state
    existing in the first place.
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    room = SessionRoom(slug="pause-regression", mode=GameMode.MULTIPLAYER)
    room.connect("alex", socket_id="sock-alex")
    room.seat("alex", character_slot="Alex")
    room._seated["alex"].state = LobbyState.PLAYING  # noqa: SLF001
    room.disconnect(socket_id="sock-alex")

    # Pre-existing predicates must continue to fire.
    assert "alex" in room.absent_seated_player_ids(), (
        "absent_seated_player_ids() must include playing-but-disconnected peers — "
        "the pause-banner reads this"
    )
    assert room.is_paused() is True, (
        "is_paused() must return True when a playing peer is disconnected"
    )


def test_is_paused_false_when_only_chargen_peer_disconnects() -> None:
    """REGRESSION (AC6): chargen-abandonment is NOT a pause condition.

    A peer who never committed a character has no in-world presence to
    pause around. The seat moves to ABANDONED; the game continues for
    the remaining playing peers. `is_paused()` must NOT trigger purely
    because of an abandoned chargen seat.

    Today's `is_paused()` is a function of `absent_seated_player_ids()`.
    The fix can either filter ABANDONED out of `absent_seated_player_ids()`
    or rewrite `is_paused()` against the new predicate. Either way, the
    observable contract is what this test asserts.
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    room = SessionRoom(slug="abandon-not-pause", mode=GameMode.MULTIPLAYER)
    # Two peers — one playing, one in chargen.
    room.connect("rux", socket_id="sock-rux")
    room.seat("rux", character_slot="Rux")
    room._seated["rux"].state = LobbyState.PLAYING  # noqa: SLF001

    room.connect("hant", socket_id="sock-hant")
    room.seat("hant", character_slot="Hant")
    # hant stays in CHARGEN (default after seat()).

    # hant abandons (mid-chargen WS drop).
    room.disconnect(socket_id="sock-hant")
    assert room._seated["hant"].state == LobbyState.ABANDONED  # noqa: SLF001

    # rux is still connected and playing → not paused.
    assert room.is_paused() is False, (
        "is_paused() must NOT be triggered by an ABANDONED seat — the slot "
        "is reclaimable, not paused"
    )


# ---------------------------------------------------------------------------
# OTEL spans (AC5)
# ---------------------------------------------------------------------------


def test_lobby_state_transition_span_fires_on_seat() -> None:
    """`lobby.state_transition` must fire when `room.seat()` transitions a
    peer from CONNECTED → CHARGEN. Attributes per spec:
      - player_id
      - from_state
      - to_state
      - reason

    Captured by patching `publish_event` on the watcher hub — wherever the
    Dev wires the emit (session_room or session_handler), the event must
    flow through the hub.

    RED today: no span emitted.
    """
    captured: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict, *, component: str = "") -> None:
        captured.append((name, payload))

    original = _hub.publish_event
    _hub.publish_event = _capture  # type: ignore[assignment]
    try:
        room = SessionRoom(slug="otel-fixture", mode=GameMode.MULTIPLAYER)
        room.connect("rux", socket_id="sock-rux")
        room.seat("rux", character_slot="Rux")
    finally:
        _hub.publish_event = original  # type: ignore[assignment]

    # The transition span must fire at least once with the right shape.
    transitions = [(name, p) for name, p in captured if name == "lobby.state_transition"]
    assert transitions, (
        f"lobby.state_transition must fire on seat(); captured={[n for n,_ in captured]}"
    )
    # At least one of the captured transitions must describe the seat call.
    seat_transition = [
        p for _, p in transitions if p.get("to_state") in ("chargen", "CHARGEN")
    ]
    assert seat_transition, (
        f"At least one lobby.state_transition must report to_state=chargen; got {transitions}"
    )
    payload = seat_transition[0]
    assert payload.get("player_id") == "rux"
    assert payload.get("from_state") in ("connected", "CONNECTED", "claiming_seat", "CLAIMING_SEAT")


def test_lobby_seat_abandoned_span_fires_on_chargen_disconnect() -> None:
    """`lobby.seat_abandoned` must fire when `disconnect()` transitions a
    peer from CHARGEN → ABANDONED (the whole point of fix dimension #4).

    Attributes per spec: `player_id`, `character_slot`, `from_state`
    (always `"chargen"` for this span — it's the convenience event for
    GM-panel filtering).

    RED today: no span emitted.
    """
    captured: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict, *, component: str = "") -> None:
        captured.append((name, payload))

    original = _hub.publish_event
    _hub.publish_event = _capture  # type: ignore[assignment]
    try:
        room = SessionRoom(slug="abandoned-fixture", mode=GameMode.MULTIPLAYER)
        room.connect("hant", socket_id="sock-hant")
        room.seat("hant", character_slot="Hant")
        room.disconnect(socket_id="sock-hant")
    finally:
        _hub.publish_event = original  # type: ignore[assignment]

    abandoned = [p for name, p in captured if name == "lobby.seat_abandoned"]
    assert abandoned, (
        f"lobby.seat_abandoned must fire on chargen disconnect; "
        f"captured={[n for n,_ in captured]}"
    )
    payload = abandoned[0]
    assert payload.get("player_id") == "hant"
    assert payload.get("character_slot") == "Hant"
    assert payload.get("from_state") in ("chargen", "CHARGEN"), (
        "from_state on lobby.seat_abandoned is always 'chargen' (per spec — "
        "this span is the convenience event for the chargen → abandoned edge)"
    )


def test_no_seat_abandoned_span_when_playing_peer_disconnects() -> None:
    """Negative test: `lobby.seat_abandoned` must NOT fire when a `playing`
    peer disconnects (their seat stays held; only pause semantics apply).

    Distinguishes "always emit on disconnect" from the spec's intent.
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    captured: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict, *, component: str = "") -> None:
        captured.append((name, payload))

    original = _hub.publish_event
    _hub.publish_event = _capture  # type: ignore[assignment]
    try:
        room = SessionRoom(slug="no-abandon", mode=GameMode.MULTIPLAYER)
        room.connect("rux", socket_id="sock-rux")
        room.seat("rux", character_slot="Rux")
        room._seated["rux"].state = LobbyState.PLAYING  # noqa: SLF001
        room.disconnect(socket_id="sock-rux")
    finally:
        _hub.publish_event = original  # type: ignore[assignment]

    abandoned = [p for name, p in captured if name == "lobby.seat_abandoned"]
    assert not abandoned, (
        f"lobby.seat_abandoned must NOT fire when a playing peer disconnects; "
        f"got {abandoned}"
    )


# ---------------------------------------------------------------------------
# Predicate boundary check
# ---------------------------------------------------------------------------


def test_abandoned_seats_excluded_from_seated_player_count_for_barrier() -> None:
    """The architectural assertion: once `playing_player_count()` is the
    barrier predicate, ABANDONED seats must not contribute to it.

    Mixed-state room: 1 PLAYING, 2 CHARGEN, 1 ABANDONED → playing == 1.
    This is the evropi scenario flattened to predicates.
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    room = SessionRoom(slug="evropi-flattened", mode=GameMode.MULTIPLAYER)

    # rux: PLAYING (committed character).
    room.connect("rux", socket_id="sock-rux")
    room.seat("rux", character_slot="Rux")
    room._seated["rux"].state = LobbyState.PLAYING  # noqa: SLF001

    # prot_thokk + pumblestone: still in CHARGEN.
    room.connect("prot_thokk", socket_id="sock-pt")
    room.seat("prot_thokk", character_slot="ProtThokk")
    room.connect("pumblestone", socket_id="sock-pb")
    room.seat("pumblestone", character_slot="Pumblestone")

    # hant: was in chargen, then dropped → ABANDONED.
    room.connect("hant", socket_id="sock-hant")
    room.seat("hant", character_slot="Hant")
    room.disconnect(socket_id="sock-hant")
    assert room._seated["hant"].state == LobbyState.ABANDONED  # noqa: SLF001

    # The barrier predicate sees ONE playing peer.
    assert room.playing_player_count() == 1, (
        f"Only PLAYING peers count toward the barrier; "
        f"playing_player_ids={room.playing_player_ids()}"
    )
    # The legacy seated_player_count is unchanged in count (it just sees
    # the dict size) — we are not redefining what 'seated' means.
    # `seated_player_count()` therefore still reports 4. The fix is at
    # the call site (session_handler.py:3222), not by mutating the
    # legacy predicate.
    assert room.seated_player_count() == 4


def test_transition_to_playing_emits_state_transition_span() -> None:
    """`transition_to_playing()` must emit a `lobby.state_transition` span
    with `to_state=playing` and `reason=chargen_complete` (AC5).

    This is the most load-bearing transition for the GM panel — the
    "chargen committed" edge — because Sebastien sees this fire when the
    barrier predicate flips from "phantom in chargen" to "active player."
    Without this test, an implementation that silently no-ops the OTEL
    emit (or uses the wrong reason string) would not be caught.

    Companion to `test_lobby_state_transition_span_fires_on_seat`, which
    covers the (new) → CONNECTED → CHARGEN edges. This test pins the
    CHARGEN → PLAYING edge explicitly.
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    captured: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict, *, component: str = "") -> None:
        captured.append((name, payload))

    original = _hub.publish_event
    _hub.publish_event = _capture  # type: ignore[assignment]
    try:
        room = SessionRoom(slug="transition-otel", mode=GameMode.MULTIPLAYER)
        room.connect("rux", socket_id="sock-rux")
        room.seat("rux", character_slot="Rux")  # → CHARGEN
        # Drop captures from connect()/seat() so the assertion below
        # operates only on the transition_to_playing event.
        captured.clear()
        room.transition_to_playing("rux")  # CHARGEN → PLAYING
    finally:
        _hub.publish_event = original  # type: ignore[assignment]

    transitions = [(name, p) for name, p in captured if name == "lobby.state_transition"]
    assert transitions, (
        f"transition_to_playing() must emit lobby.state_transition; "
        f"captured={[n for n, _ in captured]}"
    )
    # Expect exactly one transition for this single state change.
    assert len(transitions) == 1, (
        f"Exactly one lobby.state_transition must fire per transition_to_playing() "
        f"call; got {len(transitions)}: {transitions}"
    )
    payload = transitions[0][1]
    assert payload.get("player_id") == "rux"
    assert payload.get("from_state") in ("chargen", "CHARGEN"), (
        f"from_state must be 'chargen' (the prior _Seat state); got {payload}"
    )
    assert payload.get("to_state") in ("playing", "PLAYING"), (
        f"to_state must be 'playing'; got {payload}"
    )
    assert payload.get("reason") == "chargen_complete", (
        f"reason must be 'chargen_complete' so the GM panel can distinguish "
        f"this transition from PLAYER_SEAT-driven transitions; got {payload}"
    )


def test_transition_to_playing_is_idempotent_no_duplicate_span() -> None:
    """`transition_to_playing()` is documented as idempotent: a no-op when
    the seat is already PLAYING. The negative-case assertion: a duplicate
    call must NOT emit a second `lobby.state_transition` event (otherwise
    the GM panel would see a phantom CHARGEN → PLAYING transition for a
    seat that was already in PLAYING).

    Distinguishes correctness from "fire on every call regardless of
    state."
    """
    from sidequest.server.session_room import LobbyState  # type: ignore[attr-defined]

    captured: list[tuple[str, dict]] = []

    def _capture(name: str, payload: dict, *, component: str = "") -> None:
        captured.append((name, payload))

    room = SessionRoom(slug="transition-idempotent", mode=GameMode.MULTIPLAYER)
    room.connect("rux", socket_id="sock-rux")
    room.seat("rux", character_slot="Rux")
    room.transition_to_playing("rux")
    assert room._seated["rux"].state == LobbyState.PLAYING  # noqa: SLF001

    # Now patch and call again — must be a silent no-op.
    original = _hub.publish_event
    _hub.publish_event = _capture  # type: ignore[assignment]
    try:
        room.transition_to_playing("rux")  # already PLAYING — no-op
    finally:
        _hub.publish_event = original  # type: ignore[assignment]

    transitions = [(n, p) for n, p in captured if n == "lobby.state_transition"]
    assert not transitions, (
        f"Duplicate transition_to_playing() on already-PLAYING seat must NOT "
        f"emit a second state_transition event; got {transitions}"
    )
