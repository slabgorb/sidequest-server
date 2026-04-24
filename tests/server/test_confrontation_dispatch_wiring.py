"""Task 11: CONFRONTATION message dispatched on encounter begin/active/end.

These tests mock the orchestrator — no LLM call — and assert the handler
pushes a single ConfrontationMessage into the outbound list per transition.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sidequest.agents.orchestrator import BeatSelection, NarrationTurnResult
from sidequest.genre.loader import load_genre_pack
from sidequest.protocol.messages import ConfrontationMessage

# Fixture pack on disk — cache-free reload to sidestep the session-wide
# GenreLoader cache that other tests can poison with real-content CAC.
_FIXTURE_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "test_genre"
)


def _result(narration: str = "ok", **kwargs) -> NarrationTurnResult:
    return NarrationTurnResult(narration=narration, **kwargs)


@pytest.mark.asyncio
async def test_confrontation_message_emitted_on_encounter_start(
    session_handler_factory,
):
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(confrontation="combat"),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "I attack the goblins!", _build_turn_context(sd),
    )
    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(conf) == 1
    assert conf[0].payload.active is True
    assert conf[0].payload.type == "combat"
    assert [b["id"] for b in conf[0].payload.beats]  # beats included


@pytest.mark.asyncio
async def test_confrontation_message_active_false_when_resolved(
    session_handler_factory,
):
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum", current=9, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    sd.snapshot.encounter = enc
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(
            beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
        ),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "Press the attack!", _build_turn_context(sd),
    )
    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(conf) == 1
    assert conf[0].payload.active is False


@pytest.mark.asyncio
async def test_no_confrontation_message_when_state_unchanged(
    session_handler_factory,
):
    """No encounter before and no encounter after → no CONFRONTATION message."""
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(narration="You take a quiet walk."),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "Walk quietly.", _build_turn_context(sd),
    )
    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(conf) == 0


@pytest.mark.asyncio
async def test_confrontation_message_refreshed_on_live_to_live(
    session_handler_factory,
):
    """A live encounter that stays live still emits CONFRONTATION with updated
    metric — the UI needs the new payload each turn to repaint beats/bars."""
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum", current=0, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    sd.snapshot.encounter = enc
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(
            beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
        ),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "Swing again.", _build_turn_context(sd),
    )
    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(conf) == 1
    assert conf[0].payload.active is True
    # attack metric_delta=2 → momentum 0+2=2, still inside ±10 bounds.
    assert conf[0].payload.metric["current"] == 2


# ---------------------------------------------------------------------------
# ADR-074 dice integration — wiring test
# (pingpong 2026-04-24 — "Momentum increments on a failed Use Mutation roll")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dice_turn_filters_narrator_beat_selections(
    session_handler_factory,
):
    """Wiring test: when ``sd.pending_roll_outcome`` is set at narration
    time (dice-replay turn), all narrator-extracted ``beat_selections``
    are filtered so the turn has exactly one mechanical event — the
    player's rolled beat, applied by DICE_THROW dispatch.

    Regression for playtest 2026-04-24 pingpong entries "Player
    auto-plays 'attack' beat after failed Flank" and "Confrontation
    tab disappears mid-fight (resolved_encounter=False)". The
    pre-filter behavior let the narrator push momentum past threshold
    via invisible NPC beat extractions, silently resolving the
    encounter mid-fight.

    Verifies the full path: ``_execute_narration_turn`` reads
    ``pending_roll_outcome``, threads ``dice_failed`` into
    ``_apply_narration_result_to_snapshot``, which drops the
    narrator's beat_selections and leaves the encounter untouched.
    """
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    # Bypass the session-wide pack cache — otherwise a prior test that
    # loaded real-content caverns_and_claudes leaves us with a pack that
    # has no ``mutant_ability`` beat (see tests/server/conftest.py pack
    # cache comment). load_genre_pack() reads directly from disk.
    sd.genre_pack = load_genre_pack(_FIXTURE_PACK)
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum", current=0, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    sd.snapshot.encounter = enc

    # Stash a Fail-classified outcome. The handler reads via
    # ``getattr(sd, "pending_roll_outcome", None)`` + ``outcome.name``, so a
    # SimpleNamespace with ``.name = "Fail"`` is a faithful duck-typed stand-in
    # for ``RollOutcome.Fail``.
    sd.pending_roll_outcome = SimpleNamespace(name="Fail")

    # Narrator extracted beats for both player and NPC. Both must be
    # filtered out on a dice-replay turn — dice was the mechanical event.
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(
            beat_selections=[
                BeatSelection(actor="Rux", beat_id="mutant_ability", target=None),
                BeatSelection(actor="Warden", beat_id="mutant_ability", target=None),
            ],
        ),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "[BEAT_RESOLVED] Use Mutation (Instinct): ...", _build_turn_context(sd),
    )

    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(conf) == 1
    # No narrator beat applied — momentum stays at pre-narration 0.
    assert conf[0].payload.metric["current"] == 0

    # Consumed — pending outcome is cleared after the turn so the next beat
    # doesn't re-use a stale roll.
    assert sd.pending_roll_outcome is None


# ---------------------------------------------------------------------------
# CHAPTER_MARKER emission on narrator-driven location change
# (pingpong 2026-04-24 — "Location not rendered in the header on resume")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narration_with_location_emits_chapter_marker(
    session_handler_factory,
):
    """When the narrator sets ``result.location``, the outbound frames
    include a CHAPTER_MARKER carrying the new location. The UI's
    ``useRunningHeader`` hook reads CHAPTER_MARKER events — without
    this emission the running-header chapter title stays blank.

    Wiring guard: this is the server-side half of a half-wired feature
    (the UI hook existed; the server never emitted). Regression here
    would silently break the running header again.
    """
    from sidequest.protocol.enums import MessageType
    from sidequest.protocol.messages import ChapterMarkerMessage

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(
            narration="You step through the arch.",
            location="The Inner Sanctum",
        ),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "Move forward.", _build_turn_context(sd),
    )

    chapter = [m for m in msgs if isinstance(m, ChapterMarkerMessage)]
    assert len(chapter) == 1, (
        f"Expected exactly one CHAPTER_MARKER for a location change; got "
        f"{[type(m).__name__ for m in msgs]}"
    )
    assert chapter[0].type == MessageType.CHAPTER_MARKER
    assert chapter[0].payload.location == "The Inner Sanctum"


@pytest.mark.asyncio
async def test_narration_without_location_skips_chapter_marker(
    session_handler_factory,
):
    """Narration turns that do NOT change the location must not emit
    a CHAPTER_MARKER — the UI's hook would clobber the prior title
    if we emitted with the unchanged location on every turn. Scoped
    to actual location changes only.
    """
    from sidequest.protocol.messages import ChapterMarkerMessage

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(narration="You look around."),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "Look.", _build_turn_context(sd),
    )
    chapter = [m for m in msgs if isinstance(m, ChapterMarkerMessage)]
    assert chapter == []


@pytest.mark.asyncio
async def test_dice_turn_success_also_filters_narrator_beats(
    session_handler_factory,
):
    """Success roll branch of the narrator-beat filter. Same contract as
    the Fail branch — the dice roll is the mechanical event, the
    narrator's beat_selections are narrative-only. Guards against
    accidentally applying the filter only on Fail and missing the
    Success side.
    """
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.genre_pack = load_genre_pack(_FIXTURE_PACK)
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum", current=3, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    sd.snapshot.encounter = enc
    sd.pending_roll_outcome = SimpleNamespace(name="Success")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(
            beat_selections=[
                BeatSelection(actor="Warden", beat_id="mutant_ability", target=None),
            ],
        ),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "[BEAT_RESOLVED] ...", _build_turn_context(sd),
    )
    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    # Momentum stays at 3 — the narrator's Warden beat is filtered on a
    # dice-replay turn.
    assert conf[0].payload.metric["current"] == 3
