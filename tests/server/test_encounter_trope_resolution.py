"""Task 15 coverage artifact for trope-driven encounter resolution wiring.

Tests in this file duplicate tests in test_encounter_lifecycle.py intentionally.
They serve as a named artifact for Task 15's specification (story 3.4), explicitly
marking trope resolution as testable coverage when the trope engine port lands.
"""

from __future__ import annotations

from sidequest.game.encounter import EncounterActor, EncounterMetric, StructuredEncounter
from sidequest.game.session import GameSnapshot


def _make_combat_enc(*, current: int = 0) -> StructuredEncounter:
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=current, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        actors=[EncounterActor(name="Rux", role="combatant", side="player")],
    )


def test_resolve_from_trope_marks_resolved() -> None:
    """Trope completion resolves the active encounter and records the trope_id."""
    from sidequest.server.dispatch.encounter_lifecycle import (
        resolve_encounter_from_trope,
    )

    snap = GameSnapshot(genre_slug="cac")
    enc = _make_combat_enc()
    snap.encounter = enc
    result = resolve_encounter_from_trope(snapshot=snap, trope_id="last_stand")
    assert result is enc
    assert enc.resolved is True
    assert "last_stand" in (enc.outcome or "")


def test_resolve_from_trope_no_encounter_returns_none() -> None:
    """When no encounter is active, trope completion is a no-op."""
    from sidequest.server.dispatch.encounter_lifecycle import (
        resolve_encounter_from_trope,
    )

    snap = GameSnapshot(genre_slug="cac")
    assert resolve_encounter_from_trope(snapshot=snap, trope_id="x") is None


def test_resolve_from_trope_already_resolved_returns_none() -> None:
    """When encounter is already resolved, trope completion does not double-resolve."""
    from sidequest.server.dispatch.encounter_lifecycle import (
        resolve_encounter_from_trope,
    )

    snap = GameSnapshot(genre_slug="cac")
    enc = _make_combat_enc()
    enc.resolved = True
    snap.encounter = enc
    assert resolve_encounter_from_trope(snapshot=snap, trope_id="x") is None
