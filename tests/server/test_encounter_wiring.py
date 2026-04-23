"""Encounter wiring tests — narrator → snapshot.encounter → TurnContext.

Covers the three cases the playtest exposed:

1. Narrator hints ``confrontation="combat"`` → server instantiates a
   :class:`StructuredEncounter` from the matching ``ConfrontationDef``
   and writes it to ``snapshot.encounter``.
2. Encounter active + narrator emitted ``beat_selections`` → each beat's
   ``metric_delta`` lands on the live encounter's metric.
3. Metric crossed threshold (or resolution beat fired) → encounter
   resolves and ``snapshot.encounter`` is cleared.

Plus one wiring test: ``_build_turn_context`` flips ``in_combat=True``
when an encounter is active and looks up the matching ``ConfrontationDef``
so the narrator prompt can list beats.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sidequest.agents.orchestrator import BeatSelection, NarrationTurnResult, NpcMention
from sidequest.game.encounter import StructuredEncounter
from sidequest.game.session import GameSnapshot, TurnManager
from sidequest.genre.models.rules import BeatDef, ConfrontationDef, MetricDef
from sidequest.server.session_handler import (
    _SessionData,
    _build_turn_context,
    _find_confrontation_def,
    apply_encounter_updates,
)


def _make_combat_def(
    *,
    starting: int = 0,
    threshold_high: int | None = 10,
    threshold_low: int | None = -10,
) -> ConfrontationDef:
    return ConfrontationDef(
        type="combat",
        label="Test Brawl",
        category="combat",
        metric=MetricDef(
            name="momentum",
            direction="bidirectional",
            starting=starting,
            threshold_high=threshold_high,
            threshold_low=threshold_low,
        ),
        beats=[
            BeatDef(id="attack", label="Attack", metric_delta=2, stat_check="Brawn"),
            BeatDef(id="defend", label="Defend", metric_delta=1, stat_check="Toughness"),
            BeatDef(
                id="flee",
                label="Flee",
                metric_delta=0,
                stat_check="Reflexes",
                resolution=True,
            ),
        ],
    )


def _make_pack_with(def_: ConfrontationDef) -> MagicMock:
    pack = MagicMock()
    pack.rules.confrontations = [def_]
    return pack


def _make_snapshot() -> GameSnapshot:
    return GameSnapshot(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        location="Nest Crack",
        turn_manager=TurnManager(interaction=3),
    )


def test_narrator_hint_starts_encounter() -> None:
    """Narrator emits ``confrontation='combat'`` on an empty encounter
    state → server materializes a ``StructuredEncounter`` from the pack."""
    snap = _make_snapshot()
    pack = _make_pack_with(_make_combat_def(starting=0))
    result = NarrationTurnResult(
        narration="...",
        confrontation="combat",
        npcs_present=[NpcMention(name="brood-mother", role="combatant")],
    )
    apply_encounter_updates(snap, result, pack, player_name="Rux")
    assert isinstance(snap.encounter, StructuredEncounter)
    assert snap.encounter.encounter_type == "combat"
    assert snap.encounter.metric.name == "momentum"
    assert snap.encounter.metric.current == 0
    # Player is always first actor; hostile NPC is second.
    names = [a.name for a in snap.encounter.actors]
    assert "Rux" in names
    assert "brood-mother" in names


def test_narrator_hint_with_unknown_type_is_skipped() -> None:
    """No matching ``ConfrontationDef`` → leave the encounter empty and
    log a watcher event. Don't silently start something the pack never
    defined."""
    snap = _make_snapshot()
    pack = _make_pack_with(_make_combat_def())  # only 'combat'
    result = NarrationTurnResult(narration="...", confrontation="swordfight")
    apply_encounter_updates(snap, result, pack, player_name="Rux")
    assert snap.encounter is None


def test_beat_selections_apply_metric_delta() -> None:
    """Two attack beats should move the metric by 2*2 = +4."""
    snap = _make_snapshot()
    pack = _make_pack_with(_make_combat_def(starting=0))
    # Start the encounter.
    apply_encounter_updates(
        snap,
        NarrationTurnResult(narration="...", confrontation="combat"),
        pack,
        player_name="Rux",
    )
    assert snap.encounter is not None
    # Advance — two attacks this turn.
    apply_encounter_updates(
        snap,
        NarrationTurnResult(
            narration="...",
            beat_selections=[
                BeatSelection(actor="Rux", beat_id="attack"),
                BeatSelection(actor="brood-mother", beat_id="attack"),
            ],
        ),
        pack,
        player_name="Rux",
    )
    # Encounter is still active (metric hasn't crossed ±10).
    assert snap.encounter is not None
    assert snap.encounter.metric.current == 4
    assert snap.encounter.beat == 2


def test_threshold_crossing_resolves_encounter() -> None:
    """Pushing the metric past ``threshold_high`` clears
    ``snapshot.encounter`` — the encounter is over."""
    snap = _make_snapshot()
    pack = _make_pack_with(_make_combat_def(starting=9, threshold_high=10))
    apply_encounter_updates(
        snap,
        NarrationTurnResult(narration="...", confrontation="combat"),
        pack,
        player_name="Rux",
    )
    assert snap.encounter is not None
    apply_encounter_updates(
        snap,
        NarrationTurnResult(
            narration="...",
            beat_selections=[BeatSelection(actor="Rux", beat_id="attack")],
        ),
        pack,
        player_name="Rux",
    )
    # Metric went 9 → 11, crossed threshold_high=10, encounter resolved.
    assert snap.encounter is None


def test_resolution_beat_ends_encounter() -> None:
    """A beat flagged ``resolution=true`` (e.g. ``flee``) ends the
    encounter regardless of the metric value."""
    snap = _make_snapshot()
    pack = _make_pack_with(_make_combat_def(starting=0))
    apply_encounter_updates(
        snap,
        NarrationTurnResult(narration="...", confrontation="combat"),
        pack,
        player_name="Rux",
    )
    assert snap.encounter is not None
    apply_encounter_updates(
        snap,
        NarrationTurnResult(
            narration="...",
            beat_selections=[BeatSelection(actor="Rux", beat_id="flee")],
        ),
        pack,
        player_name="Rux",
    )
    assert snap.encounter is None


def test_turn_context_flips_in_combat_when_encounter_active() -> None:
    """``_build_turn_context`` must read ``snapshot.encounter`` and set
    ``in_combat=True`` + look up the matching ``ConfrontationDef`` so
    the narrator prompt lists beats. Before this wire-up, ``in_combat``
    was hardcoded False and ``confrontation_def`` was never looked up —
    why every combat turn fired with ``beat_selections=[]``."""
    snap = _make_snapshot()
    snap.encounter = StructuredEncounter.combat(combatants=["brood-mother"], hp=10)
    pack = _make_pack_with(_make_combat_def())
    pack.prompts = MagicMock()
    sd = _SessionData(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        player_name="Rux",
        player_id="p-1",
        snapshot=snap,
        store=MagicMock(),
        genre_pack=pack,
        orchestrator=MagicMock(),
    )
    ctx = _build_turn_context(sd)
    assert ctx.in_combat is True
    assert ctx.in_chase is False
    assert ctx.in_encounter is True
    assert ctx.confrontation_def is not None
    assert ctx.confrontation_def.confrontation_type == "combat"


def test_find_confrontation_def_returns_none_for_unknown_type() -> None:
    pack = _make_pack_with(_make_combat_def())
    assert _find_confrontation_def(pack, "combat") is not None
    assert _find_confrontation_def(pack, "swordfight") is None
