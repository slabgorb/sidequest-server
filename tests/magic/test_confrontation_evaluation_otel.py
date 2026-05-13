"""Confrontation auto-fire evaluation OTEL — sprint 3 cold-subsystem audit.

The bar-threshold auto-fire evaluator (``evaluate_auto_fire_triggers``)
runs every turn that lands a magic working. Per-firing watcher events
existed already, but evaluations where 0 confrontations matched were
silent — the GM panel could not distinguish "engine engaged, nothing
matched" from "engine never ran."

This test pins the new ``confrontation_evaluation`` watcher event that
fires once per ``apply_magic_working`` call and reports the candidate
count + fired count.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from sidequest.game.session import GameSnapshot
from sidequest.magic.confrontations import ConfrontationDefinition
from sidequest.magic.models import HardLimit, WorldMagicConfig
from sidequest.magic.state import MagicState


def _confrontation(
    *, id: str, trigger: str | None = "sanity <= 0.40", auto_fire: bool = True
) -> ConfrontationDefinition:
    return ConfrontationDefinition(
        id=id,
        label=id.replace("_", " ").title(),
        plugin_tie_ins=["innate_v1"],
        auto_fire=auto_fire,
        auto_fire_trigger=trigger,
        rounds=1,
        resource_pool={"primary": "sanity"},
        description="x",
        outcomes={
            "clear_win": {"mandatory_outputs": ["sanity_decrement"]},
            "pyrrhic_win": {"mandatory_outputs": ["sanity_decrement"]},
            "clear_loss": {"mandatory_outputs": ["sanity_decrement"]},
            "refused": {"mandatory_outputs": ["sanity_decrement"]},
        },
    )


@pytest.fixture
def coyote_world_config(world_config: WorldMagicConfig) -> WorldMagicConfig:
    augmented = list(world_config.hard_limits) + [
        HardLimit(id="no_resurrection", description="death is permanent"),
    ]
    return world_config.model_copy(update={"hard_limits": augmented})


@pytest.fixture
def coyote_snapshot_with_confrontations(coyote_world_config: WorldMagicConfig) -> GameSnapshot:
    state = MagicState.from_config(coyote_world_config)
    state.add_character("sira_mendes")
    state.confrontations = [
        _confrontation(id="the_bleeding_through", trigger="sanity <= 0.40"),
        _confrontation(id="the_quiet_word", trigger="notice >= 0.75"),
    ]
    return GameSnapshot.model_construct(magic_state=state)


@pytest.fixture
def captured_watcher_events(monkeypatch) -> Iterator[list[dict[str, Any]]]:
    captured: list[dict[str, Any]] = []

    def _capture(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append(
            {
                "event_type": event_type,
                "fields": fields,
                "component": component,
                "severity": severity,
            }
        )

    from sidequest.server import narration_apply

    monkeypatch.setattr(narration_apply, "_watcher_publish", _capture)
    yield captured


def _evaluation_events(captured: list[dict]) -> list[dict]:
    return [
        e
        for e in captured
        if e["component"] == "magic"
        and e["event_type"] == "state_transition"
        and e["fields"].get("op") == "confrontation_evaluation"
    ]


def test_evaluation_event_fires_even_when_no_confrontations_match(
    coyote_snapshot_with_confrontations: GameSnapshot,
    captured_watcher_events: list[dict],
) -> None:
    """Sanity bar starts at 1.0 — no trigger matches. Without the new
    evaluation event the GM panel could not see that the engine ran."""
    from sidequest.server.narration_apply import apply_magic_working

    apply_magic_working(
        snapshot=coyote_snapshot_with_confrontations,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.05},  # 1.0 → 0.95, no trigger crosses
            "domain": "psychic",
            "narrator_basis": "x",
            "flavor": "acquired",
            "consent_state": "involuntary",
        },
    )

    evals = _evaluation_events(captured_watcher_events)
    assert len(evals) == 1, (
        f"expected exactly one confrontation_evaluation event, got {len(evals)}: "
        f"{[e['fields'] for e in captured_watcher_events]}"
    )
    fields = evals[0]["fields"]
    assert fields["actor"] == "sira_mendes"
    assert fields["candidates_total"] == 2
    assert fields["candidates_for_actor"] == 2  # both bars on this actor
    assert fields["fired_count"] == 0


def test_evaluation_event_reports_fired_count_when_trigger_matches(
    coyote_snapshot_with_confrontations: GameSnapshot,
    captured_watcher_events: list[dict],
) -> None:
    """A working that drops sanity below the 0.40 threshold must fire
    ``the_bleeding_through``. The evaluation event reports fired_count=1
    so the GM panel sees the engine matched without grepping per-firing
    events."""
    from sidequest.server.narration_apply import apply_magic_working

    apply_magic_working(
        snapshot=coyote_snapshot_with_confrontations,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.7},  # 1.0 → 0.30, crosses 0.40
            "domain": "psychic",
            "narrator_basis": "x",
            "flavor": "acquired",
            "consent_state": "involuntary",
        },
    )

    evals = _evaluation_events(captured_watcher_events)
    assert len(evals) == 1
    fields = evals[0]["fields"]
    assert fields["candidates_total"] == 2
    assert fields["fired_count"] == 1
