"""Multiplayer party status / acting-character / turn-context wiring.

Covers the Phase 2 fixes from the 2026-04-24 Mawdeep playtest:

- ``_resolve_acting_character_name`` identifies the requesting socket's
  PC by ``player_id`` via the room's seat map (not by guessing
  ``snapshot.characters[0]`` which is arbitrary across commit order).
- ``_build_turn_context`` builds ``party_peers`` excluding the acting
  PC, so the narrator no longer absorbs the peer as a hireling.
- ``_build_session_start_party_status`` enumerates every PC in the
  snapshot, mapping each character_slot back to its owning player_id
  via the room.

These three together close the gap behind the playtest bugs:
"Party panel only shows self" and "Narrator absorbs peer as hireling".
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, EdgePool, Inventory
from sidequest.game.persistence import GameMode
from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.genre.loader import load_genre_pack
from sidequest.server.session_handler import (
    WebSocketSessionHandler,
    _build_turn_context,
    _resolve_acting_character_name,
    _SessionData,
)
from sidequest.server.session_room import SessionRoom

CONTENT_GENRE_PACKS = (
    Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
)


def _char(name: str) -> Character:
    return Character(
        core=CreatureCore(
            name=name,
            description="d",
            personality="p",
            inventory=Inventory(),
            edge=EdgePool(current=10, max=10, base_max=10),
        ),
        backstory=f"{name}'s tale.",
        char_class="Delver",
        race="Human",
    )


def _sd(player_id: str, player_name: str, characters: list[Character]) -> _SessionData:
    return _SessionData(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        player_name=player_name,
        player_id=player_id,
        snapshot=GameSnapshot(
            genre_slug="caverns_and_claudes",
            world_slug="mawdeep",
            location="Test",
            turn_manager=TurnManager(interaction=1),
            characters=list(characters),
        ),
        store=MagicMock(),
        genre_pack=load_genre_pack(CONTENT_GENRE_PACKS / "caverns_and_claudes"),
        orchestrator=MagicMock(),
        mode=GameMode.MULTIPLAYER,
    )


def test_acting_character_resolved_via_room_seat_map() -> None:
    """When a room is bound, the acting PC is identified by player_id."""
    laverne = _char("Laverne")
    shirley = _char("Shirley")
    room = SessionRoom(slug="2026-04-24-mawdeep-mp", mode=GameMode.MULTIPLAYER)
    room.seat("p:laverne", character_slot="Laverne")
    room.seat("p:shirley", character_slot="Shirley")

    sd_p2 = _sd("p:shirley", "Shirley", [laverne, shirley])
    # snapshot.characters[0] is Laverne — naively returning the first char
    # would mis-identify the acting player. Resolver must pick Shirley.
    assert _resolve_acting_character_name(sd_p2, room) == "Shirley"


def test_acting_character_falls_back_to_first_when_no_room() -> None:
    """Solo / pre-MP path: no room → return first character (legacy)."""
    pc = _char("Lonewolf")
    sd = _sd("p:lonewolf", "Lonewolf", [pc])
    assert _resolve_acting_character_name(sd, room=None) == "Lonewolf"


def test_acting_character_returns_player_name_when_snapshot_empty() -> None:
    """Empty snapshot.characters: return lobby player_name."""
    sd = _sd("p:none", "Newbie", [])
    assert _resolve_acting_character_name(sd, room=None) == "Newbie"


def test_party_peers_excludes_acting_pc_in_multiplayer() -> None:
    """Party-peer block must not include the acting socket's own PC."""
    laverne = _char("Laverne")
    shirley = _char("Shirley")
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.seat("p:laverne", character_slot="Laverne")
    room.seat("p:shirley", character_slot="Shirley")

    # Player 2's session: party_peers should be [Laverne], not [Shirley]
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])
    ctx = _build_turn_context(sd, room=room)
    peer_names = [p.name for p in ctx.party_peers]
    assert peer_names == ["Laverne"]
    assert ctx.character_name == "Shirley"


def test_party_peers_empty_in_solo() -> None:
    """Solo session with one PC produces no party peers."""
    pc = _char("Solo")
    sd = _sd("p:solo", "Solo", [pc])
    ctx = _build_turn_context(sd, room=None)
    assert ctx.party_peers == []
    assert ctx.character_name == "Solo"


def test_party_status_enumerates_all_pcs_in_multiplayer() -> None:
    """_build_session_start_party_status returns one PartyMember per PC,
    with the requesting socket's PC first and peers mapped via the room
    seat table.
    """
    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    room = SessionRoom(slug="2026-04-24-mawdeep-mp", mode=GameMode.MULTIPLAYER)
    room.seat("p:laverne", character_slot="Laverne")
    room.seat("p:shirley", character_slot="Shirley")
    handler._room = room

    msg = handler._build_session_start_party_status(sd, shirley, "p:shirley")
    members = msg.payload.members
    # Self first, then peer
    assert [str(m.character_name) for m in members] == ["Shirley", "Laverne"]
    # Peer player_id resolved from seat map (not the synthetic peer:<name>)
    laverne_member = members[1]
    assert str(laverne_member.player_id) == "p:laverne"
    # Self is the requesting socket's player_id
    shirley_member = members[0]
    assert str(shirley_member.player_id) == "p:shirley"


def test_party_status_falls_back_to_synthetic_peer_id_when_no_seat() -> None:
    """If a peer character is in the snapshot but the room has no seat
    record (e.g. pre-PLAYER_SEAT race), use a stable synthetic id rather
    than colliding on a real player_id.
    """
    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    # Room exists but only Shirley has claimed her seat
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.seat("p:shirley", character_slot="Shirley")
    handler._room = room

    msg = handler._build_session_start_party_status(sd, shirley, "p:shirley")
    members = msg.payload.members
    laverne_member = next(m for m in members if str(m.character_name) == "Laverne")
    assert str(laverne_member.player_id) == "peer:Laverne"


def test_party_status_solo_returns_single_member() -> None:
    """Solo path: single PC, no room — one member equal to the requesting PC."""
    pc = _char("Solo")
    sd = _sd("p:solo", "Solo", [pc])

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    msg = handler._build_session_start_party_status(sd, pc, "p:solo")
    assert len(msg.payload.members) == 1
    assert str(msg.payload.members[0].character_name) == "Solo"


def test_resolve_self_character_uses_player_seats_binding() -> None:
    """Playtest 2026-04-25 "Tab 2 sees Laverne (YOU)" regression test.

    snapshot has [Laverne, Shirley]; player_seats binds Shirley→Shirley.
    For sd.player_id=p:shirley the resolver must return the Shirley
    Character, NOT snapshot.characters[0] (Laverne).
    """
    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])
    sd.snapshot.player_seats = {"p:laverne": "Laverne", "p:shirley": "Shirley"}

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    resolved = handler._resolve_self_character(sd)
    assert resolved is shirley
    assert resolved is not laverne


def test_resolve_self_character_uses_room_seat_when_player_seats_empty() -> None:
    """Pre-2026-04-25 saves have empty player_seats but a live room seat.

    The resolver must fall through to the room's slot_to_player_id() so
    multi-PC snapshots still resolve correctly without a persisted
    binding.
    """
    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])
    # No player_seats (legacy snapshot).
    assert sd.snapshot.player_seats == {}

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    room = SessionRoom(slug="slug-x", mode=GameMode.MULTIPLAYER)
    room.seat("p:laverne", character_slot="Laverne")
    room.seat("p:shirley", character_slot="Shirley")
    handler._room = room

    resolved = handler._resolve_self_character(sd)
    assert resolved is shirley


def test_resolve_self_character_returns_none_for_legacy_solo() -> None:
    """Legacy solo save: no player_seats, no room. Resolver returns None,
    callers fall back to snapshot.characters[0].
    """
    pc = _char("Solo")
    sd = _sd("p:solo", "Solo", [pc])

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    assert handler._resolve_self_character(sd) is None


def test_party_status_uses_resolver_when_caller_passes_resolved_character() -> None:
    """Wiring test: turn-end / slug-resume PARTY_STATUS callers MUST pass
    the resolver's result (not snapshot.characters[0]) so the requesting
    socket's PC is tagged as 'self', not whichever PC happened to be
    appended first.

    This test simulates the exact playtest 2026-04-25 repro: two PCs in
    the snapshot, the requesting socket is the second player, and the
    PARTY_STATUS frame is built via the resolver. Self-tagged member
    must be Shirley with character_name="Shirley", not Laverne tagged
    with Shirley's player_id.
    """
    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])
    sd.snapshot.player_seats = {"p:laverne": "Laverne", "p:shirley": "Shirley"}

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    room = SessionRoom(slug="slug-x", mode=GameMode.MULTIPLAYER)
    room.seat("p:laverne", character_slot="Laverne")
    room.seat("p:shirley", character_slot="Shirley")
    handler._room = room

    # Mimic the fixed call-site pattern: resolver-or-fallback.
    self_char = (
        handler._resolve_self_character(sd)
        or sd.snapshot.characters[0]
    )
    assert self_char is shirley, (
        "Resolver must pick Shirley for sd.player_id=p:shirley — picking "
        "characters[0] (Laverne) is the bug we're regressing against"
    )

    msg = handler._build_session_start_party_status(sd, self_char, "p:shirley")
    members = msg.payload.members
    # Self comes first; self's character_name and player_id must agree.
    self_member = members[0]
    assert str(self_member.character_name) == "Shirley"
    assert str(self_member.player_id) == "p:shirley"
    # Peer is Laverne with Laverne's player_id (NOT Shirley's).
    peer_member = members[1]
    assert str(peer_member.character_name) == "Laverne"
    assert str(peer_member.player_id) == "p:laverne"
    # No two members share a player_id (the bug produced colliding ids).
    pids = [str(m.player_id) for m in members]
    assert len(set(pids)) == len(pids), (
        f"Duplicate player_id in PartyMember frame: {pids}"
    )


# ---------------------------------------------------------------------------
# _merge_peer_state_into_snapshot — playtest 2026-04-25 multi-PC persistence
# ---------------------------------------------------------------------------


def _saved_snapshot_with(characters: list[Character], seats: dict[str, str]):
    """Build a SavedGame-like duck-typed object with a `.snapshot` attribute.

    The merge helper only reads `saved.snapshot.characters` and
    `saved.snapshot.player_seats`, so a simple namespace-style dummy is
    enough — no DB.
    """
    snap = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        location="Test",
        turn_manager=TurnManager(interaction=1),
        characters=list(characters),
    )
    snap.player_seats = dict(seats)

    class _Saved:
        def __init__(self, snapshot):
            self.snapshot = snapshot
    return _Saved(snap)


def test_merge_peer_state_pulls_peer_chars_and_seats_from_persisted() -> None:
    """Playtest 2026-04-25 regression: Laverne's session has only [Laverne]
    in `sd.snapshot` but the persisted store has [Laverne, Shirley] from
    Shirley's chargen-commit. After merge, Laverne's session sees both.
    """
    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:laverne", "Laverne", [laverne])
    sd.snapshot.player_seats = {"p:laverne": "Laverne"}
    sd.store.load.return_value = _saved_snapshot_with(
        [laverne, shirley],
        {"p:laverne": "Laverne", "p:shirley": "Shirley"},
    )

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    handler._merge_peer_state_into_snapshot(sd)

    char_names = sorted(c.core.name for c in sd.snapshot.characters)
    assert char_names == ["Laverne", "Shirley"], (
        "Merge must append peer characters from persisted store"
    )
    assert sd.snapshot.player_seats == {
        "p:laverne": "Laverne",
        "p:shirley": "Shirley",
    }


def test_merge_peer_state_local_wins_for_shared_character_names() -> None:
    """Local turn deltas (HP changes, inventory) must not be overwritten
    by older persisted state. Merge appends peer-only chars; shared
    names keep the local copy.
    """
    laverne_local = _char("Laverne")
    laverne_local.core.edge.current = 3  # took damage this turn
    laverne_persisted = _char("Laverne")  # full HP, stale
    shirley = _char("Shirley")
    sd = _sd("p:laverne", "Laverne", [laverne_local])
    sd.store.load.return_value = _saved_snapshot_with(
        [laverne_persisted, shirley], {}
    )

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    handler._merge_peer_state_into_snapshot(sd)

    laverne_after = next(c for c in sd.snapshot.characters if c.core.name == "Laverne")
    assert laverne_after.core.edge.current == 3, (
        "Local Laverne (HP 3) must not be replaced by persisted Laverne (full HP)"
    )


def test_merge_peer_state_noop_for_solo() -> None:
    """SOLO sessions must not load-merge (extra DB read, plus the fix is
    only for the MP last-writer-wins problem).
    """
    pc = _char("Solo")
    sd = _sd("p:solo", "Solo", [pc])
    sd.mode = GameMode.SOLO
    # If the merge ran, it would read from store — make load() blow up.
    sd.store.load.side_effect = RuntimeError("merge should not call load() in solo")

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    handler._merge_peer_state_into_snapshot(sd)
    sd.store.load.assert_not_called()


def test_merge_peer_state_noop_when_persisted_missing() -> None:
    """First-ever save (no persisted row): load() returns None → no-op."""
    pc = _char("Solo")
    sd = _sd("p:solo", "Solo", [pc])
    sd.store.load.return_value = None

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    handler._merge_peer_state_into_snapshot(sd)
    # Snapshot unchanged.
    assert [c.core.name for c in sd.snapshot.characters] == ["Solo"]


def test_merge_peer_state_swallows_load_errors() -> None:
    """A best-effort merge must never raise out of the save path — a
    transient store error during merge would otherwise turn into a
    hard save failure for the disconnecting / turn-ending player.
    """
    laverne = _char("Laverne")
    sd = _sd("p:laverne", "Laverne", [laverne])
    sd.store.load.side_effect = RuntimeError("transient store error")

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-saves"))
    # Must not raise.
    handler._merge_peer_state_into_snapshot(sd)
    # Snapshot unchanged.
    assert [c.core.name for c in sd.snapshot.characters] == ["Laverne"]
