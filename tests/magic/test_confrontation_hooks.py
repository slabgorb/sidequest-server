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
            "mechanism": "ritual",
            "actor": "sira_mendes",
            "costs": {"notice": 0.15},
            "domain": "social",
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
