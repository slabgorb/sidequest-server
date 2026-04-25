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
