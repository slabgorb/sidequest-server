"""Tests for the narrator-driven gold_change apply seam.

Playtest 2026-05-07 wiring fix. The narrator describes economic
events in prose ("nineteen silver buys all three", "you find a
coin purse with twenty gold") and emits a ``gold_change`` integer
on its game_patch sidecar. The apply seam is responsible for:

1. Mutating ``character.core.inventory.gold`` on the acting PC
   (positive = gain, negative = spend).
2. Clamping at >= 0 — narrator-prose underflow must not push the
   purse into hidden negative-debt without an explicit tracker.
3. Emitting one ``economy.gold_change`` watcher span per change so
   Sebastien's GM panel sees the mechanical event paired with the
   prose (matches OTEL ask in the pingpong bug report).

Solo path mirrors the items_gained/items_lost lane: the rolling PC
is ``snapshot.characters[0]``. The wiring test at the bottom
asserts the production caller in ``websocket_session_handler``
(via ``_apply_narration_result_to_snapshot``) accepts a
``NarrationTurnResult`` with ``gold_change`` populated and produces
the same effects as a direct mutation.
"""

from __future__ import annotations

from typing import Any

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore
from sidequest.game.session import GameSnapshot
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot


def _core(name: str, gold: int = 0) -> CreatureCore:
    core = CreatureCore(name=name, description="X.", personality="Y.")
    core.inventory.gold = gold
    return core


def _pc(name: str, gold: int = 0) -> Character:
    return Character(
        core=_core(name, gold=gold),
        backstory="A wanderer.",
        char_class="adventurer",
        race="human",
    )


class _RecordingHub:
    """Drop-in replacement for the watcher hub that records every event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], str]] = []

    def __call__(
        self, event_type: str, payload: dict[str, Any], *, component: str
    ) -> None:
        self.events.append((event_type, payload, component))


def _patch_watcher(monkeypatch) -> _RecordingHub:
    hub = _RecordingHub()
    monkeypatch.setattr(
        "sidequest.server.narration_apply._watcher_publish", hub
    )
    return hub


class _Room:
    """Minimal SessionRoom stand-in for the apply seam's read surface."""

    def slot_to_player_id(self) -> dict[str, str]:
        return {}


# ---------------------------------------------------------------------------
# Spend (negative gold_change)
# ---------------------------------------------------------------------------


def test_spend_subtracts_from_purse_and_emits_span(monkeypatch) -> None:
    """The canonical Sünden market case: Carl pays 19sp for rope, boots,
    and dressing-fat. Purse drops 50 → 31, span fires with the delta.
    """
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl", gold=50)])
    snapshot.turn_manager.interaction = 5

    result = NarrationTurnResult(
        narration="**Brecca's Stall**\n\nNineteen silver buys all three.",
        gold_change=-19,
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        player_name="Carl",
        room=_Room(),  # type: ignore[arg-type]
        pack=None,
        acting_character_name="Carl",
    )

    assert snapshot.characters[0].core.inventory.gold == 31

    spans = [e for e in hub.events if e[1].get("kind") == "economy.gold_change"]
    assert len(spans) == 1
    payload = spans[0][1]
    assert payload["actor"] == "Carl"
    assert payload["requested_delta"] == -19
    assert payload["applied_delta"] == -19
    assert payload["before"] == 50
    assert payload["after"] == 31
    assert payload["clamped"] is False
    assert spans[0][2] == "economy"


# ---------------------------------------------------------------------------
# Gain (positive gold_change)
# ---------------------------------------------------------------------------


def test_gain_adds_to_purse(monkeypatch) -> None:
    """Found-coin case: prose declares a windfall, purse grows."""
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl", gold=10)])
    snapshot.turn_manager.interaction = 8

    result = NarrationTurnResult(
        narration="A coin purse — twenty silver, lighter than it looks.",
        gold_change=20,
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        player_name="Carl",
        room=_Room(),  # type: ignore[arg-type]
        pack=None,
        acting_character_name="Carl",
    )

    assert snapshot.characters[0].core.inventory.gold == 30
    spans = [e for e in hub.events if e[1].get("kind") == "economy.gold_change"]
    assert len(spans) == 1
    assert spans[0][1]["applied_delta"] == 20


# ---------------------------------------------------------------------------
# Clamp at zero
# ---------------------------------------------------------------------------


def test_overspend_clamps_at_zero_and_marks_span(monkeypatch) -> None:
    """A narrator that prose-spends more than the player has must not
    push the purse into hidden negative debt. The apply layer clamps
    at 0 and surfaces ``clamped=True`` so the GM panel can flag it.
    """
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl", gold=5)])
    snapshot.turn_manager.interaction = 9

    result = NarrationTurnResult(
        narration="Carl tips the rest of his purse onto the slate.",
        gold_change=-50,
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        player_name="Carl",
        room=_Room(),  # type: ignore[arg-type]
        pack=None,
        acting_character_name="Carl",
    )

    assert snapshot.characters[0].core.inventory.gold == 0
    payload = next(
        e[1] for e in hub.events if e[1].get("kind") == "economy.gold_change"
    )
    assert payload["requested_delta"] == -50
    assert payload["applied_delta"] == -5  # actual change, not the request
    assert payload["clamped"] is True


# ---------------------------------------------------------------------------
# No-ops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delta", [None, 0])
def test_zero_or_missing_delta_is_noop(monkeypatch, delta) -> None:
    """``gold_change=None`` (omitted by the narrator) and
    ``gold_change=0`` (explicit zero) must not emit a span and must
    not touch the purse — span noise on every quiet turn would drown
    out the GM panel.
    """
    hub = _patch_watcher(monkeypatch)
    snapshot = GameSnapshot(characters=[_pc("Carl", gold=42)])
    snapshot.turn_manager.interaction = 3

    result = NarrationTurnResult(
        narration="Carl sits in the doss-house, watching dust.",
        gold_change=delta,
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        player_name="Carl",
        room=_Room(),  # type: ignore[arg-type]
        pack=None,
        acting_character_name="Carl",
    )

    assert snapshot.characters[0].core.inventory.gold == 42
    assert [
        e for e in hub.events if e[1].get("kind") == "economy.gold_change"
    ] == []
