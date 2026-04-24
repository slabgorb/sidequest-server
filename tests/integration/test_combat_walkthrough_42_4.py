"""Story 42-4 AC7 — end-to-end combat walkthrough.

A single cohesive test that drives a combat encounter from engage through
metric escalation to resolution, using the narration-patch dispatch seam
``_apply_narration_result_to_snapshot``. Asserts:

  1. The engage narration creates a ``StructuredEncounter`` (AC5/41-11
     contract — confirms 42-1 type is still wired).
  2. Per-turn beat selections advance the encounter metric.
  3. Crossing the metric threshold resolves the encounter.
  4. A ``SPAN_ENCOUNTER_RESOLVED`` OTEL span is emitted at resolution —
     GM panel observability contract (Sebastien).
  5. After resolution, ``_build_turn_context`` sees ``in_combat=False``
     and ``snapshot.encounter.resolved=True`` — combat does not leak
     into subsequent turns.

Per AC7: "Integration walkthrough ends with resolution." Other tests in
``test_encounter_apply_narration.py`` cover individual ticks — this one
is the *concatenated* walkthrough that proves the pieces compose.

Out of scope for this test (covered elsewhere):
  - Full WebSocket handshake (tests/server/test_session_handler_slug_*.py)
  - Chargen pipeline (tests/server/test_session_handler_decomposer.py)
  - XP award per turn (tests/server/test_xp_award.py)
  - Narrator prompt zone assembly (tests/agents/test_orchestrator.py)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.agents.orchestrator import (
    BeatSelection,
    NarrationTurnResult,
)
from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.encounter import (
    EncounterMetric,
    MetricDirection,
    StructuredEncounter,
)
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import load_genre_pack

_FIXTURE_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "test_genre"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pack():
    return load_genre_pack(_FIXTURE_PACK)


@pytest.fixture
def bound_session(pack):
    """_SessionData with a playable Rux character ready to take a turn."""
    from sidequest.server.session_handler import _SessionData

    core = CreatureCore(
        name="Rux",
        description="A stoic fighter",
        personality="stoic",
        inventory=Inventory(),
    )
    char = Character(
        core=core,
        char_class="Fighter",
        race="Human",
        backstory="A wandering delver.",
    )
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.characters.append(char)
    return _SessionData(
        genre_slug="caverns_and_claudes",
        world_slug="crypt_of_the_seven",
        player_name="Rux",
        player_id="p1",
        snapshot=snap,
        store=MagicMock(),
        genre_pack=pack,
        orchestrator=MagicMock(),
    )


@pytest.fixture
def otel_capture():
    """Route emitted spans to an in-memory exporter for the duration of a test."""
    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), (
        f"expected SDK TracerProvider, got {type(provider)!r}"
    )
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


# ---------------------------------------------------------------------------
# The walkthrough
# ---------------------------------------------------------------------------


def test_combat_walkthrough_engage_tick_resolve(bound_session, otel_capture) -> None:
    """Engage a combat → tick metric → resolve on threshold crossing.

    AC7 end-to-end: a single sequence of narration patches drives the
    encounter from creation to resolution, with the final turn's
    TurnContext reflecting the resolution.
    """
    from sidequest.server.session_handler import (
        _apply_narration_result_to_snapshot,
        _build_turn_context,
    )

    sd = bound_session

    # --- Turn 1 — narrator opens combat ---
    engage = NarrationTurnResult(
        narration="Goblins leap from the shadows, weapons drawn.",
        confrontation="combat",
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        sd.snapshot, engage, player_name="Rux", pack=sd.genre_pack
    )
    assert sd.snapshot.encounter is not None, (
        "Engage turn did not create an encounter — 42-1 wiring regression."
    )
    assert isinstance(sd.snapshot.encounter, StructuredEncounter)
    assert sd.snapshot.encounter.encounter_type == "combat"
    assert sd.snapshot.encounter.resolved is False

    ctx_after_engage = _build_turn_context(sd)
    assert ctx_after_engage.in_combat is True, (
        "TurnContext.in_combat should be True after engage — AC1 wiring."
    )

    # --- Prime metric so a single beat carries us over the threshold ---
    # The test_genre fixture's ``attack`` beat bumps metric by +2. We need
    # the pre-beat current to be threshold_high - 2 or better so one more
    # tick resolves the encounter without needing to drive four full beats.
    sd.snapshot.encounter.metric = EncounterMetric(
        name="momentum",
        current=8,
        starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10,
        threshold_low=-10,
    )

    # --- Turn 2 — narrator selects the resolving beat ---
    resolve = NarrationTurnResult(
        narration="Rux deals the killing blow; the goblins break.",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        sd.snapshot, resolve, player_name="Rux", pack=sd.genre_pack
    )

    # AC7 assertion 1 — encounter is resolved
    assert sd.snapshot.encounter.resolved is True, (
        "Encounter did not resolve after the resolving beat. Check "
        "metric threshold logic in _apply_narration_result_to_snapshot."
    )
    assert sd.snapshot.encounter.structured_phase.value == "Resolution"

    # AC7 assertion 2 — resolution OTEL span was emitted
    span_names = [span.name for span in otel_capture.get_finished_spans()]
    assert "encounter.resolved" in span_names, (
        "Expected an ``encounter.resolved`` span on resolution; observed "
        f"{span_names!r}. Without this, GM-panel queries for resolved "
        "encounters never find anything — Sebastien's mechanical-visibility "
        "feature breaks."
    )

    # AC7 assertion 3 — next turn's context reflects resolution
    ctx_after_resolve = _build_turn_context(sd)
    assert ctx_after_resolve.in_combat is False, (
        "TurnContext.in_combat stayed True after encounter resolution — "
        "``in_combat()`` helper is not consulting encounter.resolved."
    )


def test_walkthrough_unresolved_encounter_keeps_in_combat_across_turns(
    bound_session,
) -> None:
    """A turn that doesn't resolve combat leaves ``in_combat=True``.

    Rules out the false-positive where ``in_combat`` flips False spuriously
    between turns. Guards against a regression where any beat-applied
    side-effect resets the flag.
    """
    from sidequest.server.session_handler import (
        _apply_narration_result_to_snapshot,
        _build_turn_context,
    )

    sd = bound_session
    engage = NarrationTurnResult(
        narration="Skeletons rise.", confrontation="combat", npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        sd.snapshot, engage, player_name="Rux", pack=sd.genre_pack
    )

    # Metric far from threshold — a single attack beat cannot resolve.
    sd.snapshot.encounter.metric = EncounterMetric(
        name="momentum",
        current=0,
        starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10,
        threshold_low=-10,
    )

    mid = NarrationTurnResult(
        narration="Rux hacks at the nearest skeleton.",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        sd.snapshot, mid, player_name="Rux", pack=sd.genre_pack
    )

    assert sd.snapshot.encounter.resolved is False
    ctx = _build_turn_context(sd)
    assert ctx.in_combat is True, (
        "TurnContext.in_combat flipped False on a non-resolving turn."
    )
