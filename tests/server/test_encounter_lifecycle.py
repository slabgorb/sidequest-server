from __future__ import annotations

import pytest

from sidequest.genre.loader import GenreLoader, DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.game.encounter import StructuredEncounter
from sidequest.game.session import GameSnapshot


@pytest.fixture
def cac_pack():
    return GenreLoader(DEFAULT_GENRE_PACK_SEARCH_PATHS).load("caverns_and_claudes")


def test_instantiate_combat_creates_encounter(cac_pack) -> None:
    from sidequest.server.dispatch.encounter_lifecycle import (
        instantiate_encounter_from_trigger,
    )
    snap = GameSnapshot(genre="caverns_and_claudes")
    enc = instantiate_encounter_from_trigger(
        snapshot=snap, pack=cac_pack, encounter_type="combat",
        combatants=["Rux", "Goblin"], hp=10, genre_slug="caverns_and_claudes",
    )
    assert enc is not None
    assert snap.encounter is enc
    assert enc.encounter_type == "combat"
    assert [a.name for a in enc.actors] == ["Rux", "Goblin"]
    # caverns_and_claudes combat metric is momentum (bidirectional, starts 0,
    # threshold_high=10, threshold_low=-10) — NOT the generic combat factory's hp.
    assert enc.metric.name == "momentum"
    assert enc.metric.starting == 0
    assert enc.metric.threshold_high == 10


def test_instantiate_unknown_type_raises(cac_pack) -> None:
    """CLAUDE.md: no silent fallback on unknown encounter_type."""
    from sidequest.server.dispatch.encounter_lifecycle import (
        instantiate_encounter_from_trigger,
    )
    snap = GameSnapshot(genre="caverns_and_claudes")
    with pytest.raises(ValueError, match="unknown encounter_type"):
        instantiate_encounter_from_trigger(
            snapshot=snap, pack=cac_pack, encounter_type="spelling_bee",
            combatants=["Rux"], hp=10, genre_slug="caverns_and_claudes",
        )


def test_instantiate_replaces_resolved_encounter(cac_pack) -> None:
    """A resolved prior encounter does not block a new one."""
    from sidequest.server.dispatch.encounter_lifecycle import (
        instantiate_encounter_from_trigger,
    )
    snap = GameSnapshot(genre="caverns_and_claudes")
    prior = StructuredEncounter.combat(combatants=["old"], hp=1)
    prior.resolved = True
    snap.encounter = prior
    enc = instantiate_encounter_from_trigger(
        snapshot=snap, pack=cac_pack, encounter_type="combat",
        combatants=["Rux"], hp=10, genre_slug="caverns_and_claudes",
    )
    assert snap.encounter is enc
    assert enc is not prior


def test_instantiate_active_encounter_is_noop(cac_pack) -> None:
    """If an active unresolved encounter already exists, do not clobber."""
    from sidequest.server.dispatch.encounter_lifecycle import (
        instantiate_encounter_from_trigger,
    )
    snap = GameSnapshot(genre="caverns_and_claudes")
    active = StructuredEncounter.combat(combatants=["already"], hp=10)
    snap.encounter = active
    result = instantiate_encounter_from_trigger(
        snapshot=snap, pack=cac_pack, encounter_type="combat",
        combatants=["Rux"], hp=10, genre_slug="caverns_and_claudes",
    )
    assert result is None
    assert snap.encounter is active


def test_resolve_from_trope_marks_resolved() -> None:
    from sidequest.server.dispatch.encounter_lifecycle import (
        resolve_encounter_from_trope,
    )
    snap = GameSnapshot(genre="cac")
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    snap.encounter = enc
    result = resolve_encounter_from_trope(snapshot=snap, trope_id="last_stand")
    assert result is enc
    assert enc.resolved is True
    assert "last_stand" in (enc.outcome or "")


def test_resolve_from_trope_no_encounter_returns_none() -> None:
    from sidequest.server.dispatch.encounter_lifecycle import (
        resolve_encounter_from_trope,
    )
    snap = GameSnapshot(genre="cac")
    assert resolve_encounter_from_trope(snapshot=snap, trope_id="x") is None


def test_resolve_from_trope_already_resolved_returns_none() -> None:
    from sidequest.server.dispatch.encounter_lifecycle import (
        resolve_encounter_from_trope,
    )
    snap = GameSnapshot(genre="cac")
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.resolved = True
    snap.encounter = enc
    assert resolve_encounter_from_trope(snapshot=snap, trope_id="x") is None
