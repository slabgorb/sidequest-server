"""LocalDM — structured-output decomposer between sealed-letter and narrator.

Spec: docs/superpowers/specs/2026-04-23-local-dm-decomposer-design.md §3-§7

Reads player action + game state. Emits DispatchPackage (spec §5).
Never writes prose. Runs on a persistent Haiku session (ADR-066 pattern).

Group B scope: single-player decompose (per spec §10 Group B).
Multiplayer batched decompose lands alongside Group G (multiplayer session
model spec — `cross_player` dispatch entries).
"""
from __future__ import annotations

from sidequest.protocol.dispatch import DispatchPackage


class LocalDM:
    """Local DM decomposer. Haiku-backed in Group B; local fine-tune in Group E."""

    def __init__(self) -> None:
        # Task 3 extends: client injection, session id, soul_data reference.
        pass

    async def decompose(
        self,
        *,
        turn_id: str,
        player_id: str,
        raw_action: str,
        state_summary: str,
    ) -> DispatchPackage:
        """Decompose one player action into a DispatchPackage.

        Group B Task 2 stub: returns an empty package so session-handler wiring
        (Task 10) can exercise the flow before the LLM call is implemented.
        Task 3 replaces this body with the Haiku call + structured-output parse.
        """
        return DispatchPackage(
            turn_id=turn_id,
            per_player=[],
            cross_player=[],
            confidence_global=0.0,
            degraded=True,
            degraded_reason="stub_not_yet_implemented",
        )


__all__ = ["LocalDM"]
