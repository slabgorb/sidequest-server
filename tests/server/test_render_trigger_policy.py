"""Pure-function tests for ``classify_trigger()`` — Story 45-30.

These tests are explicitly *support* for the boundary tests in
``test_render_dispatch.py``. The wire-first workflow allows unit tests
"as SUPPORT for the boundary test, not as a substitute" — and the
``classify_trigger()`` function is the single deterministic decision
function that the dispatch seam delegates to. Locking down its priority
ordering with cheap unit tests means the boundary test can focus on the
wiring rather than enumerating every signal combination.

Contract under test (from ``sprint/context/context-story-45-30.md``):

    def classify_trigger(
        result: NarrationTurnResult,
        snapshot_location_before: str | None,
        encounter_resolved_this_turn: bool,
    ) -> RenderTriggerReason: ...

Priority order (first match wins):
    BEAT_FIRE > SCENE_CHANGE > NPC_INTRO > ENCOUNTER_RESOLVED > NONE_POLICY
"""

from __future__ import annotations

import pytest

from sidequest.agents.orchestrator import (
    BeatSelection,
    NarrationTurnResult,
    NpcMention,
    VisualScene,
)


# Tests intentionally import from the new module — these imports MUST fail
# until Dev creates ``sidequest/server/render_trigger.py``. That import-time
# failure is a *correct* RED signal.
@pytest.fixture(scope="module")
def render_trigger_module():
    """Lazy-import the module so collection-time failures don't take down
    the rest of the suite. Returns the module."""
    import sidequest.server.render_trigger as mod  # noqa: PLC0415

    return mod


def _result(**kwargs) -> NarrationTurnResult:
    """A canonical ``NarrationTurnResult`` with sensible defaults so each
    test names ONLY the field it cares about. ``visual_scene`` defaults to
    a populated scene so the legacy ``visual is None`` short-circuit
    cannot mask a misclassification — the policy must actively classify
    every input."""
    defaults = {
        "narration": "test prose",
        "visual_scene": VisualScene(subject="anything"),
    }
    defaults.update(kwargs)
    return NarrationTurnResult(**defaults)


# ---------------------------------------------------------------------------
# Enum contract
# ---------------------------------------------------------------------------


def test_render_trigger_reason_enum_values_match_wire_contract(
    render_trigger_module,
) -> None:
    """The enum values are part of the wire — they ship in the watcher
    event ``reason`` field and the GM panel filters on them. Change them
    and the GM panel breaks silently."""
    R = render_trigger_module.RenderTriggerReason

    # All five canonical values present and matching the wire string.
    assert R.BEAT_FIRE.value == "beat_fire"
    assert R.SCENE_CHANGE.value == "scene_change"
    assert R.NPC_INTRO.value == "npc_intro"
    assert R.ENCOUNTER_RESOLVED.value == "resolved"
    assert R.NONE_POLICY.value == "none_policy"


# ---------------------------------------------------------------------------
# Single-signal classification (one positive signal at a time)
# ---------------------------------------------------------------------------


def test_classify_beat_fire_when_beat_selections_non_empty(
    render_trigger_module,
) -> None:
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(
        beat_selections=[BeatSelection(actor="Felix", beat_id="trap_sprung")]
    )
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason is R.BEAT_FIRE


def test_classify_scene_change_when_location_differs(
    render_trigger_module,
) -> None:
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(location="The Glass Flats")
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason is R.SCENE_CHANGE


def test_classify_no_scene_change_when_location_unchanged(
    render_trigger_module,
) -> None:
    """If ``result.location`` matches the pre-turn snapshot location, the
    NPC/beat/etc signals must take over — SCENE_CHANGE is NOT inferred
    from the field merely being present."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    base = "Tood's Dome — Nest Crack"
    result = _result(location=base)  # same as snapshot
    reason = classify(
        result,
        snapshot_location_before=base,
        encounter_resolved_this_turn=False,
    )
    assert reason is R.NONE_POLICY


def test_classify_npc_intro_only_when_is_new_true(render_trigger_module) -> None:
    """An ``NpcMention`` whose ``is_new`` is False must NOT trigger
    NPC_INTRO — that's the recurring-NPC banter case Felix's playtest
    showed (NPC named twice in a chapter doesn't earn a render)."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(npcs_present=[NpcMention(name="Sallow Dree", is_new=False)])
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason is R.NONE_POLICY

    result_new = _result(
        npcs_present=[NpcMention(name="Sallow Dree", is_new=True)]
    )
    reason_new = classify(
        result_new,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason_new is R.NPC_INTRO


def test_classify_encounter_resolved_via_boolean_kwarg(
    render_trigger_module,
) -> None:
    """ENCOUNTER_RESOLVED is the only reason whose signal is OUT-OF-BAND
    — it's threaded through ``encounter_resolved_this_turn`` because
    narration_apply derives it (see story context Reuse section)."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(confrontation="bandit_ambush")
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=True,
    )
    assert reason is R.ENCOUNTER_RESOLVED


def test_classify_none_policy_when_no_signals_present(
    render_trigger_module,
) -> None:
    """Banter turn: visual_scene present, location matches snapshot, no
    beats, no new NPCs, no encounter resolution. The classifier must
    return NONE_POLICY *even though* the narrator emitted a visual_scene
    — this is the load-bearing semantic shift from the pre-story
    ``visual is None`` short-circuit."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(location="Tood's Dome — Nest Crack")
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason is R.NONE_POLICY


# ---------------------------------------------------------------------------
# Priority ordering (multi-signal conflicts)
# ---------------------------------------------------------------------------


def test_priority_beat_fire_outranks_scene_change(
    render_trigger_module,
) -> None:
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(
        location="The Glass Flats",  # SCENE_CHANGE
        beat_selections=[BeatSelection(actor="Felix", beat_id="b")],
    )
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason is R.BEAT_FIRE


def test_priority_scene_change_outranks_npc_intro(
    render_trigger_module,
) -> None:
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(
        location="The Glass Flats",  # SCENE_CHANGE
        npcs_present=[NpcMention(name="Sallow Dree", is_new=True)],
    )
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason is R.SCENE_CHANGE


def test_priority_npc_intro_outranks_encounter_resolved(
    render_trigger_module,
) -> None:
    """If the same turn introduces a fresh NPC AND resolves an encounter,
    NPC_INTRO wins. Both are real diamonds, but the introduction is the
    longer-lived narrative beat (the NPC will likely recur); the
    resolution is implied by the prose change-of-state."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(
        npcs_present=[NpcMention(name="Sallow Dree", is_new=True)],
        confrontation="bandit_ambush",
    )
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=True,
    )
    assert reason is R.NPC_INTRO


def test_priority_full_pile_up_resolves_to_beat_fire(
    render_trigger_module,
) -> None:
    """All four positive signals at once → BEAT_FIRE (top of priority).
    This is the realistic encounter-resolution case where the climactic
    blow is also the last beat fire of the encounter."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(
        location="The Glass Flats",
        beat_selections=[
            BeatSelection(actor="Felix", beat_id="killing_blow"),
        ],
        npcs_present=[NpcMention(name="Sallow Dree", is_new=True)],
        confrontation="ambush_in_glassflats",
    )
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=True,
    )
    assert reason is R.BEAT_FIRE


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_classify_handles_none_snapshot_location_before(
    render_trigger_module,
) -> None:
    """Brand-new game — the pre-turn snapshot has no location yet. A
    result with a location should classify as SCENE_CHANGE (entering
    the world counts), not crash on ``None`` comparison."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(location="The Glass Flats")
    reason = classify(
        result,
        snapshot_location_before=None,
        encounter_resolved_this_turn=False,
    )
    assert reason is R.SCENE_CHANGE


def test_classify_handles_empty_npc_with_is_new_false(
    render_trigger_module,
) -> None:
    """Multiple NpcMentions, none new — NONE_POLICY. Catches the
    ``any(m.is_new)`` vs ``all(...)`` mistake."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(
        npcs_present=[
            NpcMention(name="A", is_new=False),
            NpcMention(name="B", is_new=False),
            NpcMention(name="C", is_new=False),
        ],
    )
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason is R.NONE_POLICY


def test_classify_npc_intro_when_any_mention_is_new(
    render_trigger_module,
) -> None:
    """Mixed list — at least one new NPC → NPC_INTRO."""
    R = render_trigger_module.RenderTriggerReason
    classify = render_trigger_module.classify_trigger

    result = _result(
        npcs_present=[
            NpcMention(name="Familiar Face", is_new=False),
            NpcMention(name="The Stranger", is_new=True),
        ],
    )
    reason = classify(
        result,
        snapshot_location_before="Tood's Dome — Nest Crack",
        encounter_resolved_this_turn=False,
    )
    assert reason is R.NPC_INTRO
