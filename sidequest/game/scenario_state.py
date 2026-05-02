"""Runtime state for an active scenario — Slice D data model.

Covers the "binding" surface only: :meth:`ScenarioState.from_genre_pack`
and its accessors. Between-turn processing (gossip, NPC autonomous
actions, clue availability), accusation evaluation, and narrator context
formatting are explicitly deferred — they have no consumer until the
narrator/between-turn pipeline lands post-Story-2.3.

Scope decisions:

- ``clue_graph`` stores the genre-level :class:`~sidequest.genre.models.scenario.ClueGraph`
  directly. A typed game-level ``ClueGraph`` with enum ``ClueType`` /
  ``DiscoveryMethod`` / ``ClueVisibility`` for :class:`ClueActivation`
  lands with the runtime that consumes it. Storing the genre form
  avoids a silent stub.
- ``adjacency`` is built fully-connected (every NPC can gossip with
  every other in the scenario).
- ``npc_roles`` keys on NPC *name* (not id). Role is ``Guilty`` for the
  chosen suspect, ``Witness`` for any NPC whose
  ``initial_beliefs.suspicions`` is non-empty, otherwise ``Innocent``.
- ``guilty_npc`` is the *id*, selected randomly from ``can_be_guilty``
  suspects. Falls back to the first NPC id if no suspect is marked
  ``can_be_guilty``.

Selection is seedable for test determinism via the optional ``rng``
argument on :meth:`from_genre_pack`.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field

from sidequest.genre.models.scenario import ClueGraph, ScenarioPack

# ---------------------------------------------------------------------------
# ScenarioRole — role a scenario assigns to a given NPC.
# ---------------------------------------------------------------------------


class ScenarioRole:
    """String constants for scenario role assignment."""

    Guilty = "guilty"
    Witness = "witness"
    Innocent = "innocent"


# ---------------------------------------------------------------------------
# ScenarioState — the bound runtime object.
# ---------------------------------------------------------------------------


class ScenarioState(BaseModel):
    """Runtime state for an active scenario bound to a game session."""

    model_config = {"extra": "forbid"}

    clue_graph: ClueGraph = Field(default_factory=ClueGraph)
    discovered_clues: set[str] = Field(default_factory=set)
    npc_roles: dict[str, str] = Field(default_factory=dict)
    guilty_npc: str = ""
    tension: float = 0.0
    resolved: bool = False
    adjacency: dict[str, list[str]] = Field(default_factory=dict)
    questioned_npcs: set[str] = Field(default_factory=set)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_genre_pack(
        cls,
        pack: ScenarioPack,
        *,
        rng: random.Random | None = None,
    ) -> ScenarioState:
        """Initialize scenario state from a genre pack's scenario pack.

        - Copies the clue graph verbatim (genre form; game-form
          conversion defers until :class:`ClueActivation` lands).
        - Chooses a guilty NPC from ``assignment_matrix.suspects``
          filtered by ``can_be_guilty``. Falls back to the first
          scenario NPC id if no suspect is eligible.
        - Builds ``npc_roles`` keyed by NPC *name*: the guilty suspect
          gets ``Guilty``, NPCs with any ``initial_beliefs.suspicions``
          become ``Witness``, the rest are ``Innocent``.
        - Builds a fully-connected adjacency graph across scenario NPC
          names for later gossip propagation.

        ``rng`` is injectable for test determinism; defaults to module
        :mod:`random`.
        """
        picker: random.Random | random.Random = rng if rng is not None else random.Random()

        # Guilty selection: prefer can_be_guilty suspects; otherwise
        # fall back to the first scenario NPC id. The "unknown"
        # fallback is unreachable for any well-formed pack, so the
        # deterministic first-NPC choice avoids a magic string.
        guilty_candidates = [s.id for s in pack.assignment_matrix.suspects if s.can_be_guilty]
        if guilty_candidates:
            guilty_npc = picker.choice(guilty_candidates)
        elif pack.npcs:
            guilty_npc = pack.npcs[0].id
        else:
            guilty_npc = ""

        # Role map keyed by NPC name.
        npc_roles: dict[str, str] = {}
        for snpc in pack.npcs:
            if snpc.id == guilty_npc:
                role = ScenarioRole.Guilty
            elif snpc.initial_beliefs.suspicions:
                role = ScenarioRole.Witness
            else:
                role = ScenarioRole.Innocent
            npc_roles[snpc.name] = role

        # Fully-connected gossip adjacency across scenario NPC names.
        names = [snpc.name for snpc in pack.npcs]
        adjacency: dict[str, list[str]] = {
            name: [other for other in names if other != name] for name in names
        }

        return cls(
            clue_graph=pack.clue_graph.model_copy(deep=True),
            discovered_clues=set(),
            npc_roles=npc_roles,
            guilty_npc=guilty_npc,
            tension=0.0,
            resolved=False,
            adjacency=adjacency,
            questioned_npcs=set(),
        )

    # ------------------------------------------------------------------
    # Mutation helpers (minimal — full between-turn logic deferred).
    # ------------------------------------------------------------------

    def set_tension(self, tension: float) -> None:
        """Set tension level, clamped to ``[0.0, 1.0]``."""
        self.tension = max(0.0, min(1.0, tension))

    def discover_clue(self, clue_id: str) -> None:
        """Mark a clue as discovered."""
        from sidequest.telemetry.spans import SPAN_SCENARIO_ADVANCE, Span

        already = clue_id in self.discovered_clues
        with Span.open(
            SPAN_SCENARIO_ADVANCE,
            {
                "clue_id": clue_id,
                "duplicate": bool(already),
                "guilty_npc": self.guilty_npc,
            },
        ) as span:
            self.discovered_clues.add(clue_id)
            span.set_attribute("discovered_total", len(self.discovered_clues))

    def record_questioned_npc(self, npc_name: str) -> None:
        """Record that the player questioned a scenario NPC."""
        self.questioned_npcs.add(npc_name)


__all__ = ["ScenarioRole", "ScenarioState"]
