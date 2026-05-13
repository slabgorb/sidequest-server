"""Disposition → attitude band mapping (ADR-020 three-tier).

A central home for the helper that translates a numeric disposition
score (-100..+100) into one of three attitude bands. Lives under
``sidequest.game`` so both the engine (``game/session.py``) and the
dispatch surface (``server/dispatch/opening.py``) can share it without
crossing layers backwards.

Boundaries are strict: 10 is neutral, 11 is friendly; -10 is neutral,
-11 is hostile. This matches ``ADR-020``'s NPC disposition system and
the prior private helper that lived inline in ``opening.py``.

Story 50-10 will replace this module with a typed ``Attitude`` enum +
``Disposition.attitude()`` derivation. Until then the string values
emitted here are the contract: ``"friendly"`` / ``"neutral"`` /
``"hostile"``. Whatever enum 50-10 lands must keep those literal values
so downstream consumers (the OTEL ``disposition.shift`` route, the GM
panel, the narrator's NPC serialization) stay stable across the cutover.
"""

from __future__ import annotations


def disposition_attitude(disposition: int) -> str:
    if disposition > 10:
        return "friendly"
    if disposition < -10:
        return "hostile"
    return "neutral"
