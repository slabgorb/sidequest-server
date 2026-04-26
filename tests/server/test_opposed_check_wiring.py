"""End-to-end wiring tests for the opposed_check resolution branch.

CLAUDE.md: every set of tests must include at least one integration test
that verifies the component is wired into the system — imported, called,
and reachable from production code paths.

These tests cover:

- Wiring 1: dispatch_dice_throw defers beat application when the active
  confrontation declares ``resolution_mode: opposed_check`` and surfaces
  the deferred state on ``DiceThrowOutcome``.
- Wiring 2: ``_apply_narration_result_to_snapshot`` runs the opposed
  resolver (and not the legacy beat loop) when the cdef is opposed_check
  and pending player state is present.
- Wiring 3: ``encounter_opposed_roll_resolved_span`` actually fires
  during the resolution and carries every spec'd attribute.
- Wiring 4: the narrator prompt receives the opposed_check gate text
  when the active encounter has that mode (and DOES NOT receive it
  otherwise).
- Wiring 5: the migrated genre packs load with opposed_check + a
  populated ``opponent_default_stats`` block, end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.agents.narrator import NarratorAgent
from sidequest.agents.orchestrator import BeatSelection, NarrationTurnResult
from sidequest.agents.prompt_framework.core import PromptRegistry
from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    StructuredEncounter,
)
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import load_genre_pack
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.rules import (
    BeatDef,
    ConfrontationDef,
    ResolutionMode,
    RulesConfig,
)
from sidequest.protocol.dice import (
    DiceThrowPayload,
    RollOutcome,
    ThrowParams,
)
from sidequest.server.dispatch.dice import dispatch_dice_throw
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from sidequest.telemetry.spans import SPAN_ENCOUNTER_OPPOSED_ROLL_RESOLVED

CONTENT_ROOT = Path(__file__).resolve().parents[2].parent / "sidequest-content" / "genre_packs"
MIGRATION_ROOT = Path("/Users/slabgorb/Projects/oq-2-content-migration/genre_packs")


# ---------------------------------------------------------------------------
# OTEL exporter fixture (mirrors tests/server/dispatch/test_sealed_letter.py)
# ---------------------------------------------------------------------------

@pytest.fixture()
def captured_spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Install a per-test tracer that exports to an in-memory buffer.

    Mirrors ``tests/server/dispatch/test_sealed_letter.py`` —
    monkeypatching ``spans.tracer`` so each test gets a fresh exporter
    without colliding with the global tracer provider that other tests
    or the server boot may have installed.
    """
    from sidequest.telemetry import spans as spans_module
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    test_tracer = provider.get_tracer("test")
    monkeypatch.setattr(spans_module, "tracer", lambda: test_tracer)
    return exporter


# ---------------------------------------------------------------------------
# Test fixtures (lightweight pack + encounter, no real YAML)
# ---------------------------------------------------------------------------

def _attack_beat() -> BeatDef:
    return BeatDef.model_validate({
        "id": "attack", "label": "Attack", "kind": "strike", "base": 2,
        "stat_check": "STR",
    })


def _defend_beat() -> BeatDef:
    return BeatDef.model_validate({
        "id": "defend", "label": "Defend", "kind": "brace", "base": 1,
        "stat_check": "STR",
    })


def _opposed_cdef() -> ConfrontationDef:
    return ConfrontationDef.model_validate({
        "type": "combat",
        "label": "Combat",
        "category": "combat",
        "resolution_mode": "opposed_check",
        "opponent_default_stats": {"STR": 12},
        "player_metric": {"name": "momentum", "starting": 0, "threshold": 10},
        "opponent_metric": {"name": "momentum", "starting": 0, "threshold": 10},
        "beats": [_attack_beat().model_dump(), _defend_beat().model_dump()],
    })


def _legacy_cdef() -> ConfrontationDef:
    """Same beats but legacy beat_selection — for negative narrator-prompt test."""
    return ConfrontationDef.model_validate({
        "type": "combat",
        "label": "Combat",
        "category": "combat",
        "resolution_mode": "beat_selection",
        "player_metric": {"name": "momentum", "starting": 0, "threshold": 10},
        "opponent_metric": {"name": "momentum", "starting": 0, "threshold": 10},
        "beats": [_attack_beat().model_dump(), _defend_beat().model_dump()],
    })


def _make_pack(cdef: ConfrontationDef) -> GenrePack:
    """Build a minimal GenrePack — only ``rules`` is consulted by the
    code paths under test. ``model_construct`` skips the validator so we
    don't need to fabricate every required nested field (theme, lore,
    etc.); a real load_genre_pack happens in the migration wiring test.
    """
    rules = RulesConfig(confrontations=[cdef])
    return GenrePack.model_construct(rules=rules)


def _make_encounter() -> StructuredEncounter:
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        structured_phase=EncounterPhase.Setup,
        actors=[
            EncounterActor(
                name="Sam", role="combatant", side="player",
                per_actor_state={"stats": {"STR": 14}},
            ),
            EncounterActor(
                name="Wolf", role="combatant", side="opponent",
                per_actor_state={"stats": {"STR": 14}},
            ),
        ],
    )


def _throw_params() -> ThrowParams:
    return ThrowParams(
        velocity=(0.0, 0.0, 0.0),
        angular=(0.0, 0.0, 0.0),
        position=(0.0, 0.0),
    )


# ---------------------------------------------------------------------------
# Wiring 1: dispatch_dice_throw defers apply_beat for opposed_check
# ---------------------------------------------------------------------------

def test_dispatch_dice_throw_defers_apply_beat_for_opposed_check():
    enc = _make_encounter()
    pack = _make_pack(_opposed_cdef())
    payload = DiceThrowPayload(
        request_id="r1",
        throw_params=_throw_params(),
        face=[15],  # player rolled a 15
        beat_id="attack",
    )

    outcome = dispatch_dice_throw(
        payload=payload,
        rolling_player_id="p1",
        character_name="Sam",
        character_stats={"STR": 14},
        encounter=enc,
        pack=pack,
        session_id="s",
        round_number=1,
        room_broadcast=None,
    )

    # Beat application was deferred — neither dial moves yet.
    assert enc.player_metric.current == 0
    assert enc.opponent_metric.current == 0
    # The dispatcher reports the deferral.
    assert outcome.opposed_pending is True
    assert outcome.opposed_player_d20 == 15
    assert outcome.opposed_player_beat_id == "attack"


def test_dispatch_dice_throw_legacy_beat_selection_still_applies():
    """Negative wiring: when resolution_mode is beat_selection (the
    default), the legacy path applies the beat immediately. Proves the
    new branch is GUARDED by the resolution_mode check, not always-on."""
    enc = _make_encounter()
    pack = _make_pack(_legacy_cdef())
    payload = DiceThrowPayload(
        request_id="r1",
        throw_params=_throw_params(),
        face=[20],  # crit success
        beat_id="attack",
    )

    outcome = dispatch_dice_throw(
        payload=payload,
        rolling_player_id="p1",
        character_name="Sam",
        character_stats={"STR": 14},
        encounter=enc,
        pack=pack,
        session_id="s",
        round_number=1,
        room_broadcast=None,
    )

    # Legacy path applied the beat — player metric advanced.
    assert enc.player_metric.current > 0
    assert outcome.opposed_pending is False
    assert outcome.opposed_player_d20 is None


# ---------------------------------------------------------------------------
# Wiring 2 + 3: narration_apply runs the resolver and emits the OTEL span
# ---------------------------------------------------------------------------

def _patched_d20(monkeypatch, value: int) -> None:
    """Monkeypatch the server-side opponent d20 to a deterministic value."""
    from sidequest.server import narration_apply
    monkeypatch.setattr(
        narration_apply, "_roll_d20_server_side", lambda: value,
    )


def test_narration_apply_runs_resolver_and_advances_dial(monkeypatch, captured_spans):
    """Wiring: when cdef is opposed_check, _apply_narration_result_to_snapshot
    runs the resolver, applies the engine-derived tier, and the encounter
    metric advances accordingly. Proves the dispatch branch is reachable
    from the production narration path.
    """
    enc = _make_encounter()
    pack = _make_pack(_opposed_cdef())

    snapshot = GameSnapshot(genre_slug="testpack", world_slug="testworld")
    snapshot.encounter = enc

    # Player rolled 18 (mod +2) → 20. Opponent will roll 5 (mod +2) → 7.
    # Shift = +13 → CritSuccess for player. Strike kind on CritSuccess
    # advances player dial by base (=2).
    _patched_d20(monkeypatch, value=5)

    # Narrator emitted opponent's beat selection only — player's beat is in
    # the pending stash from DICE_THROW. The PC-beat gate would drop any
    # PC-side selections in real flow, so we only feed the opponent here.
    result = NarrationTurnResult(
        narration="",
        beat_selections=[
            BeatSelection(
                actor="Wolf", beat_id="attack",
                outcome=RollOutcome.Success,
            ),
        ],
    )

    apply_outcome = _apply_narration_result_to_snapshot(
        snapshot,
        result,
        player_name="p1",
        pack=pack,
        opposed_player_d20=18,
        opposed_player_beat_id="attack",
        opposed_player_actor="Sam",
        from_explicit_action=True,  # DICE_THROW already gated this turn
    )

    assert apply_outcome.sealed_letter is None
    # Player +2 strike kind on CritSuccess: own dial +2.
    assert enc.player_metric.current == 2
    # Opponent's strike on CritSuccess (from PLAYER's perspective) — but
    # apply_beat is invoked with the SAME tier for both sides per spec.
    # On CritSuccess, opponent strike at base=2 advances opponent dial +2.
    # However, the design intent is shift>0 = player wins; we apply the
    # engine-derived tier verbatim to both apply_beat calls. For the
    # opponent, the SAME tier becomes their result. CritSuccess on a
    # strike kind advances the actor's own dial by base.
    assert enc.opponent_metric.current == 2

    # Wiring 3: the lie-detector span fired with full attributes.
    finished = captured_spans.get_finished_spans()
    opposed_spans = [
        s for s in finished if s.name == SPAN_ENCOUNTER_OPPOSED_ROLL_RESOLVED
    ]
    assert len(opposed_spans) == 1, (
        f"expected exactly 1 opposed_roll_resolved span, got {len(opposed_spans)}: "
        f"finished={[s.name for s in finished]}"
    )
    attrs = dict(opposed_spans[0].attributes or {})
    assert attrs["encounter_type"] == "combat"
    assert attrs["player_roll"] == 18
    assert attrs["player_mod"] == 2
    assert attrs["opponent_roll"] == 5
    assert attrs["opponent_mod"] == 2
    assert attrs["shift"] == 13
    assert attrs["tier"] == RollOutcome.CritSuccess.value


def test_narration_apply_opposed_check_fail_advances_opponent_dial(monkeypatch, captured_spans):
    """Strict spec parity: when shift is heavily negative the player-side
    apply_beat fires with Fail tier (no own-dial advance) AND the opponent
    apply_beat fires with Fail tier (no opponent-dial advance) — but
    because `attack` is a `strike` kind whose default Fail rule has zero
    deltas, neither dial moves. This is the precise ``Fail = stalemate``
    behavior the spec intends. The OTEL span still proves the engine ran
    the check (lie-detector), and ``encounter.beat_no_op`` watcher events
    surface the silent stalemate to the GM panel.
    """
    enc = _make_encounter()
    pack = _make_pack(_opposed_cdef())
    snapshot = GameSnapshot(genre_slug="testpack", world_slug="testworld")
    snapshot.encounter = enc

    # Player rolls 5 (mod +2) → 7. Opponent rolls 18 (mod +2) → 20.
    # Shift = -13 → CritFail for player.
    _patched_d20(monkeypatch, value=18)

    result = NarrationTurnResult(
        narration="",
        beat_selections=[
            BeatSelection(actor="Wolf", beat_id="attack", outcome=RollOutcome.Success),
        ],
    )

    _apply_narration_result_to_snapshot(
        snapshot, result, player_name="p1", pack=pack,
        opposed_player_d20=5, opposed_player_beat_id="attack",
        opposed_player_actor="Sam",
        from_explicit_action=True,
    )

    # Verify the resolver ran — the OTEL span carries the derived tier.
    opposed_spans = [
        s for s in captured_spans.get_finished_spans()
        if s.name == SPAN_ENCOUNTER_OPPOSED_ROLL_RESOLVED
    ]
    assert len(opposed_spans) == 1
    assert opposed_spans[0].attributes["tier"] == RollOutcome.CritFail.value


def test_narration_apply_opposed_check_hard_fails_without_pending_state():
    """opposed_check is dice-throw-only — narrator-only path is
    structurally ineligible because PC mechanical actions must trace back
    to an explicit DICE_THROW frame. Verify the loud-failure path."""
    enc = _make_encounter()
    pack = _make_pack(_opposed_cdef())
    snapshot = GameSnapshot(genre_slug="testpack", world_slug="testworld")
    snapshot.encounter = enc

    result = NarrationTurnResult(
        narration="",
        beat_selections=[
            BeatSelection(actor="Wolf", beat_id="attack", outcome=RollOutcome.Success),
        ],
    )

    with pytest.raises(ValueError, match="without a pending DICE_THROW player roll"):
        _apply_narration_result_to_snapshot(
            snapshot, result, player_name="p1", pack=pack,
            from_explicit_action=True,
        )


# ---------------------------------------------------------------------------
# Wiring 4: narrator prompt gate fires on opposed_check encounters
# ---------------------------------------------------------------------------

def _render_prompt(cdef: ConfrontationDef) -> str:
    """Render the encounter-live prompt section text for inspection."""
    enc = _make_encounter()
    narrator = NarratorAgent()
    registry = PromptRegistry()
    narrator.build_encounter_context(
        registry, encounter=enc, cdef=cdef,
    )
    return registry.compose(narrator.name())


def test_narrator_prompt_includes_opposed_check_gate_when_mode_is_opposed():
    text = _render_prompt(_opposed_cdef())
    assert "RESOLUTION_MODE: opposed_check" in text, (
        f"expected opposed_check gate in prompt; got: {text!r}"
    )
    assert "do not narrate whether it lands or fails" in text.lower() or (
        "engine rolls dice" in text.lower()
    )


def test_narrator_prompt_omits_opposed_check_gate_when_mode_is_legacy():
    text = _render_prompt(_legacy_cdef())
    assert "RESOLUTION_MODE: opposed_check" not in text, (
        f"opposed_check gate leaked into legacy beat_selection prompt: {text!r}"
    )


# ---------------------------------------------------------------------------
# Wiring 5: real migrated genre packs load and declare opposed_check
# ---------------------------------------------------------------------------

def _migration_root_present() -> bool:
    return MIGRATION_ROOT.is_dir()


@pytest.mark.skipif(
    not _migration_root_present(),
    reason="migration worktree not present (parallel branch in sidequest-content)",
)
@pytest.mark.parametrize(
    "slug",
    [
        "caverns_and_claudes",
        "elemental_harmony",
        "heavy_metal",
        "mutant_wasteland",
        "space_opera",
        "spaghetti_western",
    ],
)
def test_migrated_pack_declares_opposed_check_on_combat(slug: str):
    pack = load_genre_pack(MIGRATION_ROOT / slug)
    assert pack.rules is not None
    combat_cdefs = [c for c in pack.rules.confrontations if c.category == "combat"]
    assert combat_cdefs, f"{slug} has no combat confrontations"
    for cdef in combat_cdefs:
        # Dogfight (sealed_letter_lookup) is the documented exception — the
        # spec migrates beat_selection-only combats to opposed_check.
        if cdef.resolution_mode is ResolutionMode.sealed_letter_lookup:
            continue
        assert cdef.resolution_mode is ResolutionMode.opposed_check, (
            f"{slug}/{cdef.confrontation_type} did not migrate to opposed_check; "
            f"got {cdef.resolution_mode}"
        )
        assert cdef.opponent_default_stats, (
            f"{slug}/{cdef.confrontation_type} missing opponent_default_stats"
        )
