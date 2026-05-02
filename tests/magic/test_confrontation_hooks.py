"""apply_magic_working triggers auto-fire confrontations — Story 47-3 Task 5.3.

Wire-first: when a magic working pushes a character bar across an
auto-fire threshold, ``apply_magic_working`` must surface the firings
on its result so the dispatch pipeline can route them through
``sidequest.server.dispatch.confrontation`` without re-running the
evaluator.

Per the plan (2026-04-28-magic-system-coyote-reach-v1.md §5.3) the
result gains an ``auto_fired: list[tuple[ConfrontationDefinition, str]]``
field. This test fails today because that field doesn't exist on
``MagicApplyResult``, which makes the GREEN-phase wiring obvious to
Dev: extend the result type, populate it inside ``apply_magic_working``,
and the consumer can iterate it.
"""

from __future__ import annotations

from typing import Any

import pytest

from sidequest.magic.confrontations import ConfrontationDefinition
from sidequest.magic.models import WorldMagicConfig


def _bleeding_through() -> ConfrontationDefinition:
    return ConfrontationDefinition(
        id="the_bleeding_through",
        label="The Bleeding-Through",
        plugin_tie_ins=["innate_v1"],
        auto_fire=True,
        auto_fire_trigger="sanity <= 0.40",
        rounds=1,
        resource_pool={"primary": "sanity", "secondary": "vitality"},
        description="x",
        outcomes={
            "clear_win": {"mandatory_outputs": ["control_tier_advance"]},
            "pyrrhic_win": {"mandatory_outputs": ["control_tier_advance", "status_add_scar"]},
            "clear_loss": {"mandatory_outputs": ["status_add_scar"]},
            "refused": {"mandatory_outputs": ["sanity_decrement"]},
        },
    )


def _quiet_word() -> ConfrontationDefinition:
    return ConfrontationDefinition(
        id="the_quiet_word",
        label="The Quiet Word",
        plugin_tie_ins=["innate_v1"],
        auto_fire=True,
        auto_fire_trigger="notice >= 0.75",
        rounds=2,
        resource_pool={"primary": "notice", "secondary": "hegemony_heat"},
        description="x",
        outcomes={
            "clear_win": {"mandatory_outputs": ["notice_decrement"]},
            "pyrrhic_win": {"mandatory_outputs": ["notice_decrement"]},
            "clear_loss": {"mandatory_outputs": ["character_scar_extracted"]},
            "refused": {"mandatory_outputs": ["notice_increment"]},
        },
    )


@pytest.fixture
def captured_watcher_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Mirror of the fixture in ``test_e2e_solo_scenario.py``.

    Captures ``narration_apply._watcher_publish`` calls so we can assert
    OTEL fan-out for confrontation firings (AC6).
    """
    captured: list[dict[str, Any]] = []

    def _capture(event_type: str, fields: dict, *, component: str = "sidequest-server", severity: str = "info") -> None:
        captured.append(
            {"event_type": event_type, "fields": fields, "component": component, "severity": severity}
        )

    from sidequest.server import narration_apply

    monkeypatch.setattr(narration_apply, "_watcher_publish", _capture)
    return captured


def test_sanity_threshold_crossing_surfaces_bleeding_through_in_result(
    world_config: WorldMagicConfig,
) -> None:
    """Crossing sanity ≤ 0.40 fires The Bleeding-Through into ``result.auto_fired``."""
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.narration_apply import apply_magic_working

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.45
    )
    # Phase 5: MagicState gains a ``confrontations`` field populated at
    # world load. Tests set it directly. (The runtime path uses
    # ``state.set_confrontations(...)`` or equivalent — choose the API
    # in the green phase; the surface tested here is read-only.)
    state.confrontations = [_bleeding_through()]

    snapshot = GameSnapshot.model_construct(magic_state=state)

    result = apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.10},
            "domain": "psychic",
            "narrator_basis": "the_bleeding_through trigger test",
            "flavor": "reflexive",
            "consent_state": "involuntary",
        },
    )

    assert hasattr(result, "auto_fired"), (
        "MagicApplyResult must expose ``auto_fired`` after Phase 5 wiring"
    )
    fired_ids = {c.id for c, _ in result.auto_fired}
    assert "the_bleeding_through" in fired_ids
    assert all(actor == "sira_mendes" for _, actor in result.auto_fired)


def test_notice_threshold_crossing_fires_quiet_word(
    world_config: WorldMagicConfig,
) -> None:
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.narration_apply import apply_magic_working

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="notice"), 0.65
    )
    state.confrontations = [_quiet_word()]

    snapshot = GameSnapshot.model_construct(magic_state=state)

    # Working that increases notice past 0.75. apply_working currently
    # consumes ``costs`` against bar values. Most worlds bind notice as
    # a cost type; if not, the green-phase implementer adapts the patch
    # field shape to whatever push-toward-threshold path apply_working
    # already supports for ``notice``.
    result = apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "relational",
            "actor": "sira_mendes",
            "costs": {"notice": 0.15},
            "domain": "divinatory",
            "narrator_basis": "the_quiet_word trigger test",
            "flavor": "loud",
            "consent_state": "voluntary",
        },
    )
    fired_ids = {c.id for c, _ in result.auto_fired}
    assert "the_quiet_word" in fired_ids


def test_sub_threshold_working_does_not_auto_fire(
    world_config: WorldMagicConfig,
) -> None:
    """A working that doesn't cross any threshold leaves ``auto_fired`` empty."""
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.narration_apply import apply_magic_working

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.95
    )
    state.confrontations = [_bleeding_through()]

    snapshot = GameSnapshot.model_construct(magic_state=state)
    result = apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.10},  # 0.95 → 0.85, no threshold crossed
            "domain": "psychic",
            "narrator_basis": "no-fire test",
            "flavor": "minor",
            "consent_state": "voluntary",
        },
    )
    assert result.auto_fired == []


def test_auto_fire_populates_pending_magic_auto_fires(
    world_config: WorldMagicConfig,
) -> None:
    """Wire-first: each auto-fire stashes a CONFRONTATION payload on the snapshot.

    The session handler drains ``pending_magic_auto_fires`` after the
    apply pipeline returns and emits one CONFRONTATION WebSocket frame
    per entry, mounting the overlay on each player's UI. Without the
    stash, players never see the auto-fired confrontation in the
    overlay (only OTEL on the GM panel).
    """
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.narration_apply import apply_magic_working

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.45
    )
    state.confrontations = [_bleeding_through()]

    snapshot = GameSnapshot.model_construct(magic_state=state)
    pre_count = len(snapshot.pending_magic_auto_fires)

    apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.10},
            "domain": "psychic",
            "narrator_basis": "auto-fire-stash test",
            "flavor": "reflexive",
            "consent_state": "involuntary",
        },
    )

    assert len(snapshot.pending_magic_auto_fires) == pre_count + 1
    payload = snapshot.pending_magic_auto_fires[-1]
    assert payload["type"] == "the_bleeding_through"
    assert payload["label"] == "The Bleeding-Through"
    assert payload["category"] == "magic_confrontation"
    assert payload["actors"] == [{"name": "sira_mendes", "role": "channeler"}]
    assert payload["player_metric"]["name"] == "sanity"
    # primary bar is sanity; bar value 0.35 → 3 in the 0-10 scale
    assert payload["player_metric"]["current"] == 3
    assert payload["player_metric"]["threshold"] == 10
    assert payload["genre_slug"] == world_config.genre_slug
    assert payload["active"] is True


def test_auto_fire_emits_otel_span(
    world_config: WorldMagicConfig,
    captured_watcher_events: list[dict[str, Any]],
) -> None:
    """AC6 + OTEL Observability Principle: every auto-fire emits a watcher event.

    Without OTEL, the GM panel cannot tell whether the system fired the
    confrontation or the narrator improvised. CLAUDE.md mandates a span
    for every subsystem decision.
    """
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.narration_apply import apply_magic_working

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.45
    )
    state.confrontations = [_bleeding_through()]

    snapshot = GameSnapshot.model_construct(magic_state=state)
    apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.10},
            "domain": "psychic",
            "narrator_basis": "OTEL fan-out test",
            "flavor": "reflexive",
            "consent_state": "involuntary",
        },
    )

    fire_events = [
        e
        for e in captured_watcher_events
        if e["component"] == "magic"
        and e["fields"].get("op") in {"confrontation_fire", "auto_fire"}
    ]
    assert fire_events, (
        "expected a magic-component watcher event with op=confrontation_fire/auto_fire; "
        f"saw events: {[(e['component'], e['fields'].get('op')) for e in captured_watcher_events]}"
    )
    fields = fire_events[0]["fields"]
    assert fields.get("confrontation_id") == "the_bleeding_through"
    assert fields.get("actor") == "sira_mendes"


def _bleeding_through_with_missing_primary_bar() -> ConfrontationDefinition:
    """A confrontation whose resource_pool primary bar isn't in the ledger.

    Trigger: ``sanity <= 0.40`` — actor HAS sanity, so the trigger
    fires correctly.

    Resource pool primary: ``willpower`` — actor does NOT have a
    willpower bar in the ledger (no plugin shipped one). Models the
    realistic content drift case: confrontations.yaml references a bar
    name that diverged from the ledger schema (typo, removed bar,
    cross-world reuse).
    """
    return ConfrontationDefinition(
        id="the_bleeding_through",
        label="The Bleeding-Through",
        plugin_tie_ins=["innate_v1"],
        auto_fire=True,
        auto_fire_trigger="sanity <= 0.40",
        rounds=1,
        # Resource pool points at a bar the actor does NOT have.
        # Trigger evaluates against ``sanity`` (which the actor has),
        # so auto_fire fires; the payload builder then asks for
        # ``willpower`` and silently returns 0.0 pre-fix.
        resource_pool={"primary": "willpower", "secondary": "vitality"},
        description="x",
        outcomes={
            "clear_win": {"mandatory_outputs": ["control_tier_advance"]},
            "pyrrhic_win": {"mandatory_outputs": ["control_tier_advance", "status_add_scar"]},
            "clear_loss": {"mandatory_outputs": ["status_add_scar"]},
            "refused": {"mandatory_outputs": ["sanity_decrement"]},
        },
    )


def test_missing_resource_pool_bar_emits_watcher_event_not_silent_zero(
    world_config: WorldMagicConfig,
    captured_watcher_events: list[dict[str, Any]],
) -> None:
    """Story 47-3 round-2 mandatory #4: ``_bar_value`` must NOT silently return 0.0.

    Pre-fix (current code at narration_apply.py:411-419):
        def _bar_value(bar_id: str) -> float:
            try:
                return magic_state.get_bar(...).value
            except KeyError:
                return 0.0          # ← silent fallback, no log, no watcher

    Direct CLAUDE.md "No Silent Fallbacks" violation. A confrontation
    whose ``resource_pool.primary`` references a bar the actor does not
    have in the ledger silently produces ``player_metric.current=0`` and
    ``player_metric.starting=0`` in the auto-fire CONFRONTATION payload.
    The player sees a zero-bar overlay; the GM panel sees nothing wrong.
    Sebastien debugging this on a Sunday playtest gets no signal.

    Post-fix expectation (Westley re-review path forward, option b):
        - The payload still constructs (no raise — the rest of the
          apply pipeline doesn't tolerate one here).
        - A watcher event is emitted to the GM panel surfacing the
          missing bar so the gap is VISIBLE not invisible.
        - The implementer chooses the op string; this test accepts any
          ``op`` whose name reflects the missing-bar event ("bar_missing",
          "bar_missing_for_payload", "missing_resource_bar", etc.).

    The test triggers the silent path realistically: a confrontation
    whose ``resource_pool.primary = "willpower"`` (a bar the actor
    doesn't have) but whose ``auto_fire_trigger`` references ``sanity``
    (which the actor does have). The trigger fires, the payload
    builder asks for ``willpower``, and pre-fix returns 0.0 silently.
    """
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.narration_apply import apply_magic_working

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    # Actor has sanity (used for trigger eval), but NOT willpower
    # (referenced by resource_pool.primary).
    state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.45
    )
    state.confrontations = [_bleeding_through_with_missing_primary_bar()]

    snapshot = GameSnapshot.model_construct(magic_state=state)

    # Drives the full path: working → costs apply → trigger evaluates →
    # auto_fire → _build_magic_confrontation_payload → _bar_value("willpower")
    # → KeyError caught.
    apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.10},
            "domain": "psychic",
            "narrator_basis": "missing-bar silent-fallback test",
            "flavor": "reflexive",
            "consent_state": "involuntary",
        },
    )

    # Sanity-check that the trigger fired and the payload was built.
    # If this assertion fails, the test setup is wrong (the trigger
    # didn't engage), not the silent-fallback under test.
    assert len(snapshot.pending_magic_auto_fires) == 1, (
        "trigger should have fired (sanity 0.35 ≤ 0.40); test setup "
        f"wrong if not — got {len(snapshot.pending_magic_auto_fires)} "
        "auto-fires"
    )
    payload = snapshot.pending_magic_auto_fires[0]
    assert payload["type"] == "the_bleeding_through"
    # The pre-fix silent return of 0.0 produces current=0; the post-fix
    # emit-watcher-and-continue path also produces current=0 (the bar
    # genuinely doesn't exist). Both paths converge on the payload
    # value — the difference is the OTEL event surfacing the gap.
    assert payload["player_metric"]["current"] == 0
    assert payload["player_metric"]["starting"] == 0

    # The actual contract under test: a watcher event must surface the
    # missing-bar case so the GM panel sees the gap. Pre-fix this list
    # contains the confrontation_fire event but NO bar-missing event.
    bar_missing_events = [
        e
        for e in captured_watcher_events
        if e["component"] == "magic"
        and "bar_missing" in str(e["fields"].get("op", "")).lower()
    ]
    assert bar_missing_events, (
        "_bar_value must emit a watcher event when the requested bar is "
        "absent from the ledger — pre-fix it returns 0.0 silently with "
        "no GM-panel signal (CLAUDE.md No-Silent-Fallbacks violation). "
        "Expected an event with component='magic' and op containing "
        "'bar_missing' (exact op name implementer's choice). "
        f"Saw events: {[(e['component'], e['fields'].get('op')) for e in captured_watcher_events]}"
    )
    fields = bar_missing_events[0]["fields"]
    assert fields.get("actor") == "sira_mendes", (
        f"watcher event must identify the actor whose bar was missing; "
        f"got fields={fields}"
    )
    assert fields.get("bar_id") == "willpower", (
        f"watcher event must identify which bar was missing so the "
        f"author can fix the resource_pool entry; got fields={fields}"
    )
    # Severity should be elevated — this is a content/config gap, not
    # routine info. WARNING or ERROR both acceptable; INFO is too quiet.
    assert bar_missing_events[0]["severity"] in {"warning", "error"}, (
        f"missing-bar event must be at WARNING or ERROR severity so "
        f"the GM panel surfaces it; got severity="
        f"{bar_missing_events[0]['severity']!r}"
    )
