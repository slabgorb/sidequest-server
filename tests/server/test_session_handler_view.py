"""Session handler projection view — zone + visibility wiring.

Verifies that ``WebSocketSessionHandler._build_game_state_view()`` pulls
zone information off the live ``GameSnapshot`` so projection-filter
predicates (``visible_to``, ``in_same_zone``) see real data rather than
the conservative ``None`` / ``False`` defaults.
"""
from __future__ import annotations

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.session import Npc


def _make_character(name: str, *, statuses: list[str] | None = None) -> Character:
    return Character(
        core=CreatureCore(
            name=name,
            description=f"{name}, test hero",
            personality="stoic",
            inventory=Inventory(),
            statuses=list(statuses or []),
        ),
        char_class="Fighter",
        race="Human",
        backstory=f"{name} wanders the test suite.",
    )


def _make_npc(name: str, *, location: str | None = None, statuses: list[str] | None = None) -> Npc:
    return Npc(
        core=CreatureCore(
            name=name,
            description=f"NPC {name}",
            personality="mysterious",
            inventory=Inventory(),
            statuses=list(statuses or []),
        ),
        location=location,
    )


def test_session_view_reflects_party_location(session_fixture) -> None:
    """All player characters share the snapshot's party-level location."""
    sd, handler = session_fixture
    sd.snapshot.characters.append(_make_character("Alice"))
    sd.snapshot.characters.append(_make_character("Bob"))
    # session_fixture already sets snapshot.location = "Main Hall".

    view = handler._build_game_state_view()

    assert view.zone_of("Alice") == "Main Hall"
    assert view.zone_of("Bob") == "Main Hall"
    # Same zone -> visible.
    assert view.visible_to("Alice", "Bob") is True


def test_session_view_reflects_npc_location(session_fixture) -> None:
    """NPCs carry their own location — the view should mirror it."""
    sd, handler = session_fixture
    sd.snapshot.characters.append(_make_character("Alice"))
    sd.snapshot.npcs.append(_make_npc("Barkeep", location="The Tavern"))

    view = handler._build_game_state_view()

    assert view.zone_of("Barkeep") == "The Tavern"
    # Alice is in "Main Hall", Barkeep in "The Tavern" -> different zones.
    assert view.visible_to("Alice", "Barkeep") is False


def test_session_view_hides_stealthed_character(session_fixture) -> None:
    """A character whose status marks them hidden is masked from visible_to."""
    sd, handler = session_fixture
    sd.snapshot.characters.append(_make_character("Alice"))
    sd.snapshot.characters.append(_make_character("Bob", statuses=["hidden"]))

    view = handler._build_game_state_view()

    # Same party zone, but Bob is hidden -> not visible.
    assert view.zone_of("Bob") == "Main Hall"
    assert view.visible_to("Alice", "Bob") is False


def test_session_view_when_snapshot_missing_returns_empty(session_fixture) -> None:
    """No session data -> conservative empty view, no exceptions."""
    _sd, handler = session_fixture
    handler._session_data = None

    view = handler._build_game_state_view()

    assert view.zone_of("Anyone") is None
    assert view.visible_to("A", "B") is False


def test_session_view_hides_stealth_npc(session_fixture) -> None:
    """NPC with a stealth-flavored status is also flagged hidden."""
    sd, handler = session_fixture
    sd.snapshot.characters.append(_make_character("Alice"))
    sd.snapshot.npcs.append(
        _make_npc("ShadowThief", location="Main Hall", statuses=["Invisible"]),
    )

    view = handler._build_game_state_view()

    assert view.zone_of("ShadowThief") == "Main Hall"
    # Co-located but hidden -> masked.
    assert view.visible_to("Alice", "ShadowThief") is False
