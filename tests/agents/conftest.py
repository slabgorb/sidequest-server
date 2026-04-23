"""Shared fixtures for tests/agents/ and its subdirectories."""
from __future__ import annotations

import pytest

from sidequest.game.session import NpcRegistryEntry


@pytest.fixture
def minimal_npc_registry() -> list[NpcRegistryEntry]:
    """A small registry list with one named NPC.

    The Python port stores the NPC registry as ``list[NpcRegistryEntry]`` on
    ``WorldSnapshot.npc_registry`` (see ``sidequest/game/session.py``).
    """
    return [
        NpcRegistryEntry(
            name="Harlan",
            role="innkeeper",
            pronouns="he/him",
            appearance="grey beard, apron",
            last_seen_location="the inn",
            last_seen_turn=1,
        ),
    ]
