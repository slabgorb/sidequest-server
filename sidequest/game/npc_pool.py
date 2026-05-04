"""NPC pool members — identity-only entries that the narrator can cite as
"people who exist in this world." Regenerable; no mechanical state. Promote
to ``Npc`` (with ``pool_origin = member.name``) when the NPC actually engages
mechanically (combat handshake, persistent dialog state).

Distinct from ``Npc`` (sidequest.game.session) which carries CreatureCore,
EdgePool, beliefs, and last-seen tracking. The split was Wave 2A of the
snapshot split-brain cleanup (spec:
docs/superpowers/specs/2026-05-04-snapshot-split-brain-cleanup-design.md).
"""

from __future__ import annotations

from pydantic import BaseModel


class NpcPoolMember(BaseModel):
    """Identity-only member of the world's NPC cast pool.

    Pool members exist as scaffolding for narrator name-continuity: when the
    narrator wants to introduce "the bartender at the Black Hart," the pool
    provides a name + appearance hook so the same character can be re-cited
    in a later narration without drift.

    Pool members are re-citable, not consumed. When the same name engages
    mechanically (combat, persistent dialog), an ``Npc`` is created with
    ``pool_origin = self.name``; the pool member remains in
    ``GameSnapshot.npc_pool`` and is shadowed by the ``Npc`` lookup at
    narration_apply time.
    """

    model_config = {"extra": "forbid"}

    name: str
    role: str | None = None
    pronouns: str | None = None
    appearance: str | None = None
    archetype_id: str | None = None
    """OTEL attribution back to the genre-pack archetype source. ``None``
    for narrator-invented members or legacy-migrated members where
    provenance was lost."""
    drawn_from: str
    """Source tag: ``"name_generator"``, ``"world_authored"``,
    ``"legacy_registry"``, ``"narrator_invented"``."""
