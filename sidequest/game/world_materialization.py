"""Campaign maturity + world materialization from genre pack history.

Maps:
- ``CampaignMaturity`` — Fresh / Early / Mid / Veteran progression tier.
- ``parse_history_chapters`` — extract a typed chapter list from raw
  pack history (history.yaml is loaded as a nested dict/value).
- ``WorldBuilder`` — fluent builder that produces a GameSnapshot at a
  given maturity by applying chapters cumulatively.
- ``materialize_world`` — apply chapters in-place to an existing
  snapshot (Story 6-6 shape).
- ``materialize_from_genre_pack`` — the dispatch-time entry point;
  parses pack history and returns a materialized snapshot.

Chapter DTOs (``HistoryChapter``, ``ChapterCharacter`` etc.) live in
``sidequest.game.history_chapter`` to avoid a circular import with
``session.py``.

No silent fallbacks: unparseable chapter data raises
``HistoryParseError``; the dispatch layer decides whether to log-and-
continue or propagate. Chapter ids outside {fresh, early, mid, veteran}
are skipped — ``CampaignMaturity.from_chapter_id`` returns None, so the
chapter doesn't match any maturity level.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from opentelemetry import trace

from sidequest.game.character import Character
from sidequest.game.creature_core import (
    CreatureCore,
    Inventory,
    placeholder_edge_pool,
)
from sidequest.game.history_chapter import (
    ChapterCharacter,
    ChapterNpc,
    ChapterTrope,
    HistoryChapter,
)
from sidequest.game.session import NarrativeEntry, Npc, TropeState


def _auto_description(race: str, char_class: str) -> str:
    """Compose the chargen-style 'A {race} {class}' description.

    Mirrors the format used by ``GenericCharacterBuilder.build`` in
    ``builder.py`` so that race-changing chapter updates can detect
    and refresh an auto-generated description (Story 45-7).
    """
    return f"A {race} {char_class}"


def _is_auto_description(description: str, race: str, char_class: str) -> bool:
    """True when a description matches the chargen auto-template format.

    Used by ``_apply_character`` to detect 'A {race} {class}' descriptions
    that should be regenerated when race/class change in a chapter update.
    Match is exact: any human-edited description (including additional
    sentences or whitespace) is preserved untouched.
    """
    return description == _auto_description(race, char_class)

# ---------------------------------------------------------------------------
# CampaignMaturity
# ---------------------------------------------------------------------------


class CampaignMaturity(StrEnum):
    """Campaign maturity tier derived from turn count + beats fired."""

    Fresh = "Fresh"
    """Turns 0-5 effective: minimal history, world is new."""

    Early = "Early"
    """Turns 6-20 effective: factions introduced, stakes emerging."""

    Mid = "Mid"
    """Turns 21-50 effective: established relationships, tensions rising."""

    Veteran = "Veteran"
    """Turns 51+ effective: deep history, faction conflicts in motion."""

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> CampaignMaturity:
        """Derive maturity from a snapshot's turn count + beats fired.

        Beats accelerate maturity: a dramatic early game matures faster.
        ``effective = round + beats / 2``.
        """
        try:
            turn = int(snapshot.turn_manager.round)
        except AttributeError:
            turn = 0
        beats = int(getattr(snapshot, "total_beats_fired", 0))
        effective = max(0, turn) + max(0, beats) // 2
        if effective <= 5:
            return cls.Fresh
        if effective <= 20:
            return cls.Early
        if effective <= 50:
            return cls.Mid
        return cls.Veteran

    @classmethod
    def from_chapter_id(cls, chapter_id: str) -> CampaignMaturity | None:
        """Map a chapter id to its maturity tier; ``None`` for unknown ids."""
        match chapter_id:
            case "fresh":
                return cls.Fresh
            case "early":
                return cls.Early
            case "mid":
                return cls.Mid
            case "veteran":
                return cls.Veteran
            case _:
                return None

    def _ordinal(self) -> int:
        """Ordering helper — chapters at or below the target tier apply."""
        order = {
            CampaignMaturity.Fresh: 0,
            CampaignMaturity.Early: 1,
            CampaignMaturity.Mid: 2,
            CampaignMaturity.Veteran: 3,
        }
        return order[self]

    def __le__(self, other: CampaignMaturity) -> bool:  # type: ignore[override]
        if not isinstance(other, CampaignMaturity):
            return NotImplemented
        return self._ordinal() <= other._ordinal()

    def __lt__(self, other: CampaignMaturity) -> bool:  # type: ignore[override]
        if not isinstance(other, CampaignMaturity):
            return NotImplemented
        return self._ordinal() < other._ordinal()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HistoryParseError(ValueError):
    """Raised when pack ``history.yaml`` chapter data can't be parsed.

    The dispatch layer catches this and logs-and-continues with an
    empty chapter list so a malformed history never hard-fails a
    session.
    """


# ---------------------------------------------------------------------------
# Parsing — history.yaml → HistoryChapter list
# ---------------------------------------------------------------------------


def parse_history_chapters(value: Any) -> list[HistoryChapter]:
    """Extract a typed chapter list from raw pack history.

    The genre pack loader stores ``history.yaml`` as an untyped dict.
    The outer shape is ``{"chapters": [...]}`` when present; we return
    an empty list for null, missing ``chapters`` key, or empty chapter
    list.
    """
    if value is None:
        return []
    if not isinstance(value, dict):
        raise HistoryParseError(
            f"history payload must be a mapping, got {type(value).__name__}"
        )
    chapters_raw = value.get("chapters")
    if chapters_raw is None:
        return []
    if not isinstance(chapters_raw, list):
        raise HistoryParseError(
            f"history 'chapters' must be a list, got {type(chapters_raw).__name__}"
        )
    parsed: list[HistoryChapter] = []
    for i, entry in enumerate(chapters_raw):
        try:
            parsed.append(HistoryChapter.model_validate(entry))
        except Exception as exc:
            raise HistoryParseError(
                f"failed to parse history chapter at index {i}: {exc!r}"
            ) from exc
    return parsed


# ---------------------------------------------------------------------------
# WorldBuilder
# ---------------------------------------------------------------------------


class WorldBuilder:
    """Fluent builder that produces a GameSnapshot at a given maturity.

    Methods:
    - ``__init__`` — default Fresh maturity, no chapters.
    - ``at_maturity`` — set the target maturity tier.
    - ``with_chapters`` — supply history chapters to apply.
    - ``build`` — materialize the snapshot, returning a new GameSnapshot.
    """

    def __init__(self) -> None:
        self.maturity: CampaignMaturity = CampaignMaturity.Fresh
        self.chapters: list[HistoryChapter] = []

    def at_maturity(self, maturity: CampaignMaturity) -> WorldBuilder:
        """Set the target campaign maturity level."""
        self.maturity = maturity
        return self

    def with_chapters(self, chapters: list[HistoryChapter]) -> WorldBuilder:
        """Provide history chapters to apply."""
        self.chapters = list(chapters)
        return self

    def build(self) -> Any:
        """Build a GameSnapshot at the configured maturity.

        Filters chapters by maturity (cumulative — includes every
        chapter whose tier is at or below the target), applies each
        chapter's data to the snapshot in order, then stores the
        applied chapters as ``snap.world_history`` so the save-file
        round-trips them.
        """
        # Local import to avoid circular dependency: session → world_materialization.
        from sidequest.game.session import GameSnapshot

        snap = GameSnapshot(campaign_maturity=self.maturity.value)

        applicable: list[HistoryChapter] = [
            ch
            for ch in self.chapters
            if (maturity := CampaignMaturity.from_chapter_id(ch.id)) is not None
            and maturity <= self.maturity
        ]

        for chapter in applicable:
            self._apply_chapter(snap, chapter)

        snap.world_history = list(applicable)
        return snap

    # ------------------------------------------------------------------
    # apply_chapter — the heart of materialization
    # ------------------------------------------------------------------

    def _apply_chapter(self, snap: Any, chapter: HistoryChapter) -> None:
        """Apply a single chapter to the snapshot, cumulatively.

        Semantics:
        - Character data populates a new Character when snapshot is empty,
          otherwise selectively updates existing fields.
        - NPCs upsert by name (update existing, else append new).
        - Quests insert/update in the ``quest_log`` dict.
        - Lore entries append to ``lore_established`` (deduplicated).
        - Notes append (no dedup).
        - Narrative log entries append, converting to ``NarrativeEntry``.
        - Scene context (location/time_of_day/atmosphere/active_stakes)
          OVERWRITES from the latest chapter that declares it — later
          chapters win.
        - Tropes upsert by definition id.
        """
        if chapter.character is not None:
            self._apply_character(snap, chapter.character)

        for npc_data in chapter.npcs:
            self._apply_npc(snap, npc_data)

        for quest_name, status in chapter.quests.items():
            snap.quest_log[quest_name] = status

        for entry in chapter.lore:
            if entry not in snap.lore_established:
                snap.lore_established.append(entry)

        snap.notes.extend(chapter.notes)

        for entry in chapter.narrative_log:
            snap.narrative_log.append(
                NarrativeEntry(
                    timestamp=0,
                    round=0,
                    author=entry.speaker,
                    content=entry.text,
                    tags=[],
                    speaker=entry.speaker,
                    entry_type=None,
                )
            )

        if chapter.location is not None:
            snap.location = chapter.location
        if chapter.time_of_day is not None:
            snap.time_of_day = chapter.time_of_day
        if chapter.atmosphere is not None:
            snap.atmosphere = chapter.atmosphere
        if chapter.active_stakes is not None:
            snap.active_stakes = chapter.active_stakes

        for trope_data in chapter.tropes:
            self._apply_trope(snap, trope_data)

    # ------------------------------------------------------------------
    # apply_character — create-or-update the player character
    # ------------------------------------------------------------------

    def _apply_character(self, snap: Any, char_data: ChapterCharacter) -> None:
        """Build or update the player character from chapter data.

        When the snapshot has no character, creates one from chapter
        fields with sensible defaults for any missing identity fields.
        When a character already exists, selectively updates in place —
        only non-empty fields on the chapter overwrite.

        Note: hp/max_hp/ac from chapter are intentionally unused — the
        placeholder EdgePool stays as-is (Epic 39 wires per-class edge
        seeding from YAML). These chapter fields are advisory defaults,
        not a silent fallback.
        """
        if not snap.characters:
            name = char_data.name if char_data.name else "Adventurer"
            race = char_data.race if char_data.race else "Human"
            cls = char_data.class_name if char_data.class_name else "Fighter"
            # Story 45-7: when chapter omits a description, fall back to
            # the chargen auto-template so the description reflects the
            # actual race/class instead of an opaque 'An adventurer.' stub.
            description = char_data.description or _auto_description(race, cls)
            personality = char_data.personality or "Determined."
            backstory = char_data.backstory or "Unknown origins."

            core = CreatureCore(
                name=name,
                description=description,
                personality=personality,
                level=max(1, char_data.level) if char_data.level else 1,
                xp=0,
                inventory=Inventory(),
                statuses=[],
                edge=placeholder_edge_pool(),
                acquired_advancements=[],
            )
            snap.characters.append(
                Character(
                    core=core,
                    backstory=backstory,
                    narrative_state="",
                    hooks=[],
                    char_class=cls,
                    race=race,
                    pronouns="",
                    stats={},
                    abilities=[],
                    affinities=[],
                    is_friendly=True,
                    known_facts=[],
                    resolved_archetype=None,
                    archetype_provenance=None,
                )
            )
            return

        # Update existing character — selective, non-empty fields only.
        char = snap.characters[0]
        # Capture pre-update race/class so we can detect auto-template
        # descriptions written against the previous identity (Story 45-7).
        prev_race = char.race
        prev_class = char.char_class
        prev_description = char.core.description
        if char_data.level > 0:
            char.core.level = char_data.level
        if char_data.name:
            char.core.name = char_data.name
        if char_data.race:
            char.race = char_data.race
        if char_data.class_name:
            char.char_class = char_data.class_name
        if char_data.backstory:
            char.backstory = char_data.backstory
        if char_data.personality:
            char.core.personality = char_data.personality
        if char_data.description:
            char.core.description = char_data.description
        else:
            # Story 45-7: if the chapter changes race or class without
            # supplying a fresh description, refresh an auto-generated
            # description ('A {race} {class}') so it tracks the new
            # identity. Hand-authored descriptions are detected by
            # comparing against the prior auto-template format and left
            # untouched. No silent fallback: only the exact prior
            # auto-template string is rewritten.
            race_or_class_changed = (
                char.race != prev_race or char.char_class != prev_class
            )
            if race_or_class_changed and _is_auto_description(
                prev_description, prev_race, prev_class
            ):
                new_desc = _auto_description(char.race, char.char_class)
                char.core.description = new_desc
                span = trace.get_current_span()
                span.add_event(
                    "world_materialization.description_refreshed",
                    {
                        "reason": "race_or_class_changed",
                        "prev_race": prev_race,
                        "prev_class": prev_class,
                        "new_race": char.race,
                        "new_class": char.char_class,
                        "prev_description": prev_description,
                        "new_description": new_desc,
                    },
                )
        # hp/max_hp/ac/gold: advisory only, see docstring.

    # ------------------------------------------------------------------
    # apply_npc — instantiate or update an NPC
    # ------------------------------------------------------------------

    def _apply_npc(self, snap: Any, npc_data: ChapterNpc) -> None:
        """Upsert an NPC by name.

        Blank name → skip (short-circuit). Existing NPC → update
        disposition, description, personality, location in place. New
        NPC → append a new ``Npc`` with chapter data and defaults for
        Phase-1-deferred fields (OCEAN, belief state, resolution tier,
        archetype axes).
        """
        if not npc_data.name:
            return

        existing: Npc | None = next(
            (n for n in snap.npcs if n.core.name == npc_data.name), None
        )
        if existing is not None:
            if npc_data.disposition is not None:
                existing.disposition = int(npc_data.disposition)
            if npc_data.description:
                existing.core.description = npc_data.description
            if npc_data.location:
                existing.location = npc_data.location
            if npc_data.personality:
                existing.core.personality = npc_data.personality
            return

        core = CreatureCore(
            name=npc_data.name,
            description=npc_data.description or "An NPC.",
            personality=npc_data.personality or "Neutral.",
            level=1,
            xp=0,
            inventory=Inventory(),
            statuses=[],
            edge=placeholder_edge_pool(),
            acquired_advancements=[],
        )
        snap.npcs.append(
            Npc(
                core=core,
                voice_id=None,
                disposition=int(npc_data.disposition or 0),
                location=npc_data.location,
                pronouns=None,
                appearance=None,
                age=None,
                build=None,
                height=None,
                distinguishing_features=[],
                ocean=None,
                resolution_tier="spawn",
                non_transactional_interactions=0,
                jungian_id=None,
                rpg_role_id=None,
                npc_role_id=None,
                resolved_archetype=None,
            )
        )

    # ------------------------------------------------------------------
    # apply_trope — upsert a trope state by definition id
    # ------------------------------------------------------------------

    def _apply_trope(self, snap: Any, trope_data: ChapterTrope) -> None:
        """Upsert a trope state on the snapshot.

        Blank id → skip. Unknown status string → defaults to "active"
        (no guard beyond the four known values). ``TropeState`` stores
        the id under ``id``.
        """
        if not trope_data.id:
            return

        status = trope_data.status
        if status not in {"dormant", "active", "progressing", "resolved"}:
            status = "active"

        existing: TropeState | None = next(
            (t for t in snap.active_tropes if t.id == trope_data.id), None
        )
        if existing is not None:
            existing.status = status
            existing.progress = float(trope_data.progression)
            return

        snap.active_tropes.append(
            TropeState(
                id=trope_data.id,
                status=status,
                progress=float(trope_data.progression),
                beats_fired=0,
            )
        )


# ---------------------------------------------------------------------------
# Stateless materialize API — the Story 6-6 shape
# ---------------------------------------------------------------------------


def materialize_world(snapshot: Any, chapters: list[HistoryChapter]) -> None:
    """Apply history chapters to a GameSnapshot based on campaign maturity.

    In-place update: calculates maturity from the current snapshot,
    filters chapters at-or-below that tier, and sets
    ``snapshot.world_history`` + ``snapshot.campaign_maturity``.
    Idempotent — safe to call repeatedly.
    """
    from sidequest.telemetry.spans import SPAN_WORLD_MATERIALIZED, Span
    maturity = CampaignMaturity.from_snapshot(snapshot)
    applicable = [
        ch
        for ch in chapters
        if (m := CampaignMaturity.from_chapter_id(ch.id)) is not None
        and m <= maturity
    ]
    with Span.open(
        SPAN_WORLD_MATERIALIZED,
        {
            "genre_slug": getattr(snapshot, "genre_slug", "") or "",
            "world_slug": getattr(snapshot, "world_slug", "") or "",
            "maturity": maturity.value,
            "chapters_input": len(chapters),
            "chapters_applied": len(applicable),
        },
    ):
        snapshot.world_history = list(applicable)
        snapshot.campaign_maturity = maturity.value


# ---------------------------------------------------------------------------
# Dispatch-time entry point
# ---------------------------------------------------------------------------


def materialize_from_genre_pack(
    history_value: Any,
    maturity: CampaignMaturity,
    genre_slug: str,
    world_slug: str,
) -> Any:
    """Parse pack history + materialize a GameSnapshot at the target maturity.

    This is the function the dispatch layer calls during chargen
    confirmation. Produces a fully materialized snapshot with
    ``genre_slug`` / ``world_slug`` set on it.

    On parse failure, raises ``HistoryParseError`` — the dispatch
    wrapper decides whether to log-and-fall-back to an empty snapshot
    or propagate. We push that decision to the caller rather than
    hiding it in a silent fallback here.
    """
    chapters = parse_history_chapters(history_value)
    snap = WorldBuilder().at_maturity(maturity).with_chapters(chapters).build()
    snap.genre_slug = genre_slug
    snap.world_slug = world_slug
    return snap
