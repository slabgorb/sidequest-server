"""History chapter DTOs — the typed shape of a ``history.yaml`` chapter.

Defines ``ChapterCharacter``, ``ChapterNpc``, ``ChapterNarrativeEntry``,
``ChapterTrope`` and ``HistoryChapter``.

This module deliberately has NO dependencies on game-state types
(Character/Npc/TropeState) — the chapter DTOs are forward-compat
pass-through structures that the WorldBuilder translates into
snapshot state. Keeping them here (rather than in ``session.py``
alongside GameSnapshot) prevents a circular import between the
materialization layer (which needs game-state types) and the
snapshot layer (which needs chapter types as field types).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChapterCharacter(BaseModel):
    """Character data within a history chapter.

    All fields optional so a chapter can declare partial character
    updates (e.g. level-only). ``class_name`` is the Python-side field;
    YAML key is ``class`` — pydantic alias handles both read and
    serialize.
    """

    model_config = {"extra": "ignore", "populate_by_name": True}

    name: str = ""
    race: str = ""
    class_name: str = Field(default="", alias="class", serialization_alias="class")
    level: int = 0
    hp: int | None = None
    max_hp: int | None = None
    ac: int | None = None
    backstory: str | None = None
    personality: str | None = None
    description: str | None = None
    gold: int | None = None


class ChapterNpc(BaseModel):
    """NPC data within a history chapter.

    ``name`` is blank-allowed because ``apply_npc`` short-circuits when
    the name is empty, so a malformed chapter entry degrades silently
    rather than exploding the whole materialization.
    """

    model_config = {"extra": "ignore"}

    name: str = ""
    role: str | None = None
    description: str | None = None
    personality: str | None = None
    disposition: int | None = None
    location: str | None = None
    backstory: str | None = None
    archetype: str | None = None
    dialogue_quirks: list[str] = Field(default_factory=list)


class ChapterNarrativeEntry(BaseModel):
    """A narrative log entry within a history chapter."""

    model_config = {"extra": "ignore"}

    speaker: str
    text: str


class ChapterTrope(BaseModel):
    """Trope state within a history chapter."""

    model_config = {"extra": "ignore"}

    id: str
    status: str
    progression: float = 0.0
    notes: list[str] = Field(default_factory=list)


class HistoryChapter(BaseModel):
    """A history chapter from the genre pack, keyed by maturity level.

    Carries everything the WorldBuilder consumes — character, NPCs,
    quests, lore, notes, narrative log, scene context, tropes — plus
    forward-compat fields (``points_of_interest`` as raw Any) the engine
    doesn't consume directly but content authors populate.
    """

    model_config = {"extra": "ignore"}

    id: str = ""
    label: str = ""
    lore: list[str] = Field(default_factory=list)
    session_range: list[int] | None = None
    character: ChapterCharacter | None = None
    npcs: list[ChapterNpc] = Field(default_factory=list)
    quests: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    narrative_log: list[ChapterNarrativeEntry] = Field(default_factory=list)
    location: str | None = None
    time_of_day: str | None = None
    atmosphere: str | None = None
    active_stakes: str | None = None
    points_of_interest: Any = None
    tropes: list[ChapterTrope] = Field(default_factory=list)
