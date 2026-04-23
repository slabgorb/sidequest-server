"""Tests for sidequest.game.combatant — Combatant typing.Protocol.

Port of sidequest-api/crates/sidequest-game/src/combatant.rs (inline
`#[cfg(test)] mod tests`). Rust `trait Combatant` becomes a Python
`typing.Protocol` with @runtime_checkable so isinstance() works for
structural typing of Character (and eventually Npc).

Test-porting discipline: every Rust test becomes one pytest function
with the same name. No idiomatic rewrites.

AC3 coverage:
- `isinstance(character, Combatant)` returns True for any Character instance
- `edge_fraction(max_edge=0)` returns 0.0 — NOT ZeroDivisionError, NOT 1.0.
  Port Rust's guard verbatim (Rust: `if self.max_edge() == 0 { return 0.0; }`).
- `is_broken` == `edge() <= 0` (port verbatim — negative edge IS broken).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from sidequest.game.combatant import Combatant

# ---------------------------------------------------------------------------
# Helper — TestCombatant mirrors the Rust test fixture struct verbatim.
# Carries Rust-verbatim semantics inline (broken@<=0, 0.0 @ max==0).
# ---------------------------------------------------------------------------


@dataclass
class _TestCombatant:
    _name: str
    _edge: int
    _max_edge: int
    _level: int

    def name(self) -> str:
        return self._name

    def edge(self) -> int:
        return self._edge

    def max_edge(self) -> int:
        return self._max_edge

    def level(self) -> int:
        return self._level

    def is_broken(self) -> bool:
        # Rust: `self.edge() <= 0` — negative edge counts as broken.
        return self.edge() <= 0

    def edge_fraction(self) -> float:
        # Rust: `if self.max_edge() == 0 { return 0.0; }`
        if self.max_edge() == 0:
            return 0.0
        return self.edge() / self.max_edge()


def warrior() -> _TestCombatant:
    return _TestCombatant(
        _name="Grog",
        _edge=20,
        _max_edge=30,
        _level=3,
    )


# ---------------------------------------------------------------------------
# Port of combatant.rs tests — function names match Rust verbatim
# ---------------------------------------------------------------------------


def test_not_broken_with_positive_edge() -> None:
    assert not warrior().is_broken()


def test_broken_at_zero_edge() -> None:
    c = _TestCombatant(_name="Grog", _edge=0, _max_edge=30, _level=3)
    assert c.is_broken()


def test_not_broken_at_one_edge() -> None:
    c = _TestCombatant(_name="Grog", _edge=1, _max_edge=30, _level=3)
    assert not c.is_broken()


def test_broken_at_negative_edge() -> None:
    """Not in Rust test suite, but the Rust trait default impl uses
    `edge <= 0`. Explicit test so a drift to `== 0` is caught — Character
    in the Python codebase already has this drift (see delivery finding)."""
    c = _TestCombatant(_name="Grog", _edge=-5, _max_edge=30, _level=3)
    assert c.is_broken()


def test_full_edge_fraction() -> None:
    c = _TestCombatant(_name="Grog", _edge=30, _max_edge=30, _level=3)
    assert c.edge_fraction() == pytest.approx(1.0)


def test_half_edge_fraction() -> None:
    c = _TestCombatant(_name="Grog", _edge=15, _max_edge=30, _level=3)
    assert c.edge_fraction() == pytest.approx(0.5)


def test_zero_edge_fraction() -> None:
    c = _TestCombatant(_name="Grog", _edge=0, _max_edge=30, _level=3)
    assert c.edge_fraction() == pytest.approx(0.0)


def test_zero_max_edge_returns_zero_fraction() -> None:
    """AC3 edge case — Rust returns 0.0 when max_edge == 0. Not
    ZeroDivisionError, not 1.0. Port verbatim. Character.edge_fraction
    returns 1.0 today — that drift is flagged in Delivery Findings."""
    c = _TestCombatant(_name="Grog", _edge=0, _max_edge=0, _level=3)
    assert c.edge_fraction() == pytest.approx(0.0)


def test_accessors_return_correct_values() -> None:
    c = warrior()
    assert c.name() == "Grog"
    assert c.edge() == 20
    assert c.max_edge() == 30
    assert c.level() == 3


# ---------------------------------------------------------------------------
# AC3: Protocol structural typing — runtime_checkable + Character conformance
# ---------------------------------------------------------------------------


def test_combatant_protocol_is_runtime_checkable() -> None:
    """Combatant MUST be @runtime_checkable so structural isinstance() works.
    Without it, Character conformance can't be verified at runtime."""
    c = warrior()
    assert isinstance(c, Combatant), (
        "Combatant must be @runtime_checkable — add the decorator in "
        "sidequest.game.combatant"
    )


def test_character_satisfies_combatant_protocol() -> None:
    """AC3: isinstance(character, Combatant) returns True for any
    Character instance."""
    from tests.game.test_character import make_test_character

    character = make_test_character()
    assert isinstance(character, Combatant), (
        "Character must structurally satisfy Combatant — exposes name(), "
        "edge(), max_edge(), level(), is_broken(), edge_fraction()"
    )


def test_combatant_protocol_rejects_type_missing_required_method() -> None:
    """Guard against the Protocol silently accepting anything.
    A type lacking one of the six required methods MUST NOT satisfy."""

    class PartiallyCombatant:
        """Missing max_edge, level, is_broken, edge_fraction."""

        def name(self) -> str:
            return "Weak"

        def edge(self) -> int:
            return 10

    c = PartiallyCombatant()
    assert not isinstance(c, Combatant)
