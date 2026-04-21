"""Combatant — structural typing.Protocol for anything that participates in
combat or composure-driven scenes.

Port of ``sidequest-api/crates/sidequest-game/src/combatant.rs`` (152 LOC).
Epic 42 / ADR-082 Phase 3.

Rust ``trait Combatant`` is translated into a Python ``typing.Protocol`` with
``@runtime_checkable`` so ``isinstance(x, Combatant)`` works for structural
typing of ``Character`` (and eventually ``Npc`` / ``Enemy``).

Per the story 42-1 design deviation (see ``.session/42-1-session.md`` ->
Design Deviations -> TEA), the Rust trait's default implementations of
``is_broken`` and ``edge_fraction`` are **not** provided as Protocol
defaults — each concrete implementer carries the two-line bodies verbatim.
The Protocol is a pure contract; ``@runtime_checkable`` only introspects
method *names* and would silently accept subtly-wrong defaults.

Rust-verbatim semantics every implementer MUST carry:

- ``is_broken()``:    ``return self.edge() <= 0``        (not ``== 0``)
- ``edge_fraction()``: ``0.0`` if ``max_edge() == 0`` (not ``1.0``, not
  ``ZeroDivisionError``)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Combatant(Protocol):
    """Common interface for combat / composure participants.

    Character, Npc, and (eventually) Enemy all satisfy this protocol
    structurally. The six methods below match
    ``sidequest_game::combatant::Combatant`` verbatim.
    """

    def name(self) -> str:
        """The combatant's display name."""
        ...

    def edge(self) -> int:
        """Current composure (EdgePool ``current``, clamped to [0, max_edge])."""
        ...

    def max_edge(self) -> int:
        """Maximum composure (EdgePool ``max``; may be mid-scene reduced)."""
        ...

    def level(self) -> int:
        """Character level."""
        ...

    def is_broken(self) -> bool:
        """Whether the combatant is broken (composure at or below zero).

        Rust default: ``self.edge() <= 0``. Negative edge MUST read as broken.
        """
        ...

    def edge_fraction(self) -> float:
        """Current composure as a fraction of max (0.0 to 1.0).

        Rust default: returns ``0.0`` when ``max_edge == 0``. Not 1.0,
        not ``ZeroDivisionError``.
        """
        ...
