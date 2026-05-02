"""EncounterTag — scene state created by ``angle`` beats and beat extras.

Spec: docs/superpowers/specs/2026-04-25-dual-track-momentum-design.md §Encounter tags.

v1: tags are created, displayed, and persisted but engine does not yet spend
leverage. v2 (story 4) adds ``consumes_leverage_from`` to BeatDef.

``target`` distinguishes per-actor tags (e.g. "The Promo is Off-Balance")
from scene-wide tags (e.g. "the floor is lava"). ``fleeting`` tags are
single-use: ``leverage`` starts at 1 and the tag vanishes when consumed
(v2). Persistent tags (``fleeting=False``) survive at ``leverage=0`` as
scene context the narrator can lean on prose-wise.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EncounterTag(BaseModel):
    """A single scene tag attached to an encounter."""

    model_config = {"extra": "forbid"}

    text: str
    created_by: str
    target: str | None = None
    leverage: int = Field(ge=0)
    fleeting: bool = False
    created_turn: int
