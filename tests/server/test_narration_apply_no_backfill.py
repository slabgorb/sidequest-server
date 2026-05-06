"""Failing tests for narration_apply backfill-defense removal (Wave 2B / AC6).

Pre-Wave-2B, ``_apply_narration_result_to_snapshot`` carried a defensive
"seed every unset peer with the OLD ``snapshot.location`` BEFORE clobbering
the global" loop (narration_apply.py:1089-1102). The defense existed because
``snapshot.location`` was the fallback for any peer who had no entry in
``character_locations``. Once the party-level location is removed (AC1), the
fallback path doesn't exist — so the defensive seed has no purpose and must
be removed (spec § "Back-fill defense removed", lines 230-232).

After Wave 2B:

- Only the acting PC's ``character_locations`` entry is updated per turn.
- A peer without an entry simply has *no* entry — callers consult the
  per-character resolver (``character_locations[name]`` / ``party_location``)
  and render "(unknown)" or "(party split)" rather than inheriting a stale
  global.
- The legacy ``character_location_seeded`` watcher event no longer fires.
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
from sidequest.server.session_handler import _SessionData
from sidequest.server.session_room import SessionRoom

CONTENT_GENRE_PACKS = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


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
    """Construct a session-data fixture WITHOUT the legacy ``location=`` kwarg.
    AC1 removes that field, so this fixture exercises the post-cleanup shape."""
    return _SessionData(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        player_name=player_name,
        player_id=player_id,
        snapshot=GameSnapshot(
            genre_slug="caverns_and_claudes",
            world_slug="mawdeep",
            turn_manager=TurnManager(interaction=1),
            characters=list(characters),
        ),
        store=MagicMock(),
        genre_pack=load_genre_pack(CONTENT_GENRE_PACKS / "caverns_and_claudes"),
        orchestrator=MagicMock(),
        mode=GameMode.MULTIPLAYER,
    )


def _seed_legacy_location(snap: GameSnapshot, value: str) -> None:
    """Set the legacy ``snapshot.location`` field if it still exists.

    Pre-AC1 (today): writes the value, which the OLD code reads to fire its
    seed loop. The "no backfill" assertions then fail under the old code,
    making RED observable.

    Post-AC1: the field is gone; this is a no-op (no field to write).
    The assertions hold trivially because the seed loop is gone.
    """
    if hasattr(snap, "location"):
        import contextlib

        with contextlib.suppress(AttributeError, ValueError):
            snap.location = value  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Backfill loop is gone — peers without entries stay without entries
# ---------------------------------------------------------------------------


def test_apply_narration_does_not_seed_unset_peer_locations() -> None:
    """AC6 — when the acting PC narrates a location update, the apply path
    must NOT seed peers' ``character_locations`` entries. A peer without an
    entry stays without an entry; the per-character resolver returns None
    and the UI renders "(unknown)" rather than inheriting whichever player
    most recently narrated.
    """
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.server.narration_apply import _apply_narration_result_to_snapshot

    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])
    # Pre-AC1: seed the legacy global so the OLD seed loop fires under
    # current code (RED). Post-AC1: no-op (field gone, loop removed).
    _seed_legacy_location(sd.snapshot, "Galley")
    sd.snapshot.character_locations = {}  # neither PC has narrated yet
    sd.snapshot.player_seats = {"p:laverne": "Laverne", "p:shirley": "Shirley"}

    room = SessionRoom(slug="slug-noseed", mode=GameMode.MULTIPLAYER)
    room.seat("p:laverne", character_slot="Laverne")
    room.seat("p:shirley", character_slot="Shirley")

    result = NarrationTurnResult(
        narration="Shirley walks to the cockpit.",
        location="Cockpit",
    )

    _apply_narration_result_to_snapshot(
        sd.snapshot,
        result,
        sd.player_name,
        room=room,
        pack=sd.genre_pack,
        acting_character_name="Shirley",
    )

    # Acting PC writes its own entry; peer remains absent.
    assert sd.snapshot.character_locations.get("Shirley") == "Cockpit"
    assert "Laverne" not in sd.snapshot.character_locations, (
        "Wave 2B AC6: backfill seed loop must be removed — peer must NOT "
        "inherit acting PC's prior location"
    )


def test_apply_narration_does_not_emit_character_location_seeded_event() -> None:
    """AC6 wire test — the ``character_location_seeded`` watcher event was
    emitted from inside the seed loop. With the loop removed, the event
    must not appear in any narration apply call."""
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.server import narration_apply as napply

    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])
    _seed_legacy_location(sd.snapshot, "Galley")
    sd.snapshot.character_locations = {}
    sd.snapshot.player_seats = {"p:laverne": "Laverne", "p:shirley": "Shirley"}

    seen_events: list[tuple[str, dict]] = []
    original = napply._watcher_publish

    def capture(event_type, payload, **kw):
        seen_events.append((event_type, dict(payload) if isinstance(payload, dict) else {}))
        return original(event_type, payload, **kw)

    napply._watcher_publish = capture  # type: ignore[assignment]
    try:
        room = SessionRoom(slug="slug-noseed-event", mode=GameMode.MULTIPLAYER)
        room.seat("p:laverne", character_slot="Laverne")
        room.seat("p:shirley", character_slot="Shirley")
        result = NarrationTurnResult(
            narration="Shirley walks to the cockpit.",
            location="Cockpit",
        )
        napply._apply_narration_result_to_snapshot(
            sd.snapshot,
            result,
            sd.player_name,
            room=room,
            pack=sd.genre_pack,
            acting_character_name="Shirley",
        )
    finally:
        napply._watcher_publish = original  # type: ignore[assignment]

    seeded = [
        payload
        for _et, payload in seen_events
        if payload.get("kind") == "character_location_seeded"
    ]
    assert seeded == [], (
        "Wave 2B AC6: ``character_location_seeded`` event must NOT be "
        "emitted — the seed loop is removed"
    )


# ---------------------------------------------------------------------------
# Acting PC still gets its own entry (no regression)
# ---------------------------------------------------------------------------


def test_apply_narration_writes_acting_character_location_entry() -> None:
    """Regression-guard: removing the seed loop must NOT remove the acting
    PC's own ``character_locations`` write — that's the canonical update
    path for Wave 2B."""
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.server.narration_apply import _apply_narration_result_to_snapshot

    laverne = _char("Laverne")
    shirley = _char("Shirley")
    sd = _sd("p:shirley", "Shirley", [laverne, shirley])
    sd.snapshot.character_locations = {"Laverne": "Engine Room"}
    sd.snapshot.player_seats = {"p:laverne": "Laverne", "p:shirley": "Shirley"}

    room = SessionRoom(slug="slug-actor-write", mode=GameMode.MULTIPLAYER)
    room.seat("p:laverne", character_slot="Laverne")
    room.seat("p:shirley", character_slot="Shirley")
    result = NarrationTurnResult(
        narration="Shirley moves up to the cockpit.",
        location="Cockpit",
    )

    _apply_narration_result_to_snapshot(
        sd.snapshot,
        result,
        sd.player_name,
        room=room,
        pack=sd.genre_pack,
        acting_character_name="Shirley",
    )

    assert sd.snapshot.character_locations["Shirley"] == "Cockpit"
    # Pre-existing peer entry untouched (no clobber, but also no seed).
    assert sd.snapshot.character_locations["Laverne"] == "Engine Room"
