"""Mandatory advancement output dispatcher — Story 47-3 Task 5.4.

Each ``mandatory_output`` ID listed in a ConfrontationDefinition's branch
maps to a handler that mutates GameSnapshot. Per the plan
(2026-04-28-magic-system-coyote-reach-v1.md §5.4), the dispatcher lives
in ``sidequest.magic.outputs`` and exposes:

    apply_mandatory_outputs(*, snapshot, outputs, actor, **context) -> None

Unknown outputs raise ``OutputUnknownError`` — no silent fallback per
CLAUDE.md.

Note: the session description names the module ``outcomes.py`` but the
plan (cited as the source of truth in the session) names it
``outputs.py``. These tests follow the plan; deviation logged in the
session ``Design Deviations`` section.
"""

from __future__ import annotations

from typing import Any

import pytest

from sidequest.magic.models import WorldMagicConfig
from sidequest.magic.outputs import (
    OutputUnknownError,
    apply_mandatory_outputs,
)


@pytest.fixture
def coyote_snapshot(world_config: WorldMagicConfig):
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import MagicState

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    return GameSnapshot.model_construct(magic_state=state)


@pytest.fixture
def captured_watcher_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture watcher events emitted during outcome application.

    AC6 / OTEL Observability Principle: every output emission MUST emit
    a span so the GM panel can verify the dispatcher is engaged rather
    than the narrator improvising.
    """
    captured: list[dict[str, Any]] = []

    def _capture(event_type: str, fields: dict, *, component: str = "sidequest-server", severity: str = "info") -> None:
        captured.append(
            {"event_type": event_type, "fields": fields, "component": component, "severity": severity}
        )

    # The handlers may publish via narration_apply._watcher_publish (existing
    # idiom) or a new sidequest.magic.outputs._watcher_publish. Patch both so
    # whichever the green-phase implementer chooses, the test still observes.
    from sidequest.server import narration_apply as _na

    monkeypatch.setattr(_na, "_watcher_publish", _capture, raising=False)
    try:
        from sidequest.magic import outputs as _outputs

        if hasattr(_outputs, "_watcher_publish"):
            monkeypatch.setattr(_outputs, "_watcher_publish", _capture, raising=False)
    except ImportError:
        pass

    return captured


def test_sanity_decrement_debits_bar(coyote_snapshot) -> None:
    from sidequest.magic.state import BarKey

    apply_mandatory_outputs(
        snapshot=coyote_snapshot,
        outputs=["sanity_decrement"],
        actor="sira_mendes",
    )
    sanity = coyote_snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    )
    # Default sanity_decrement = 0.10; sanity starts_at_chargen 1.0.
    assert sanity.value == pytest.approx(0.90)


def test_sanity_increment_credits_bar(coyote_snapshot) -> None:
    from sidequest.magic.state import BarKey

    # First push it down so increment has somewhere to go.
    coyote_snapshot.magic_state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.50
    )

    apply_mandatory_outputs(
        snapshot=coyote_snapshot,
        outputs=["sanity_increment"],
        actor="sira_mendes",
    )
    sanity = coyote_snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    )
    assert sanity.value > 0.50


def test_unknown_output_raises(coyote_snapshot) -> None:
    """Lang-review #1: silent exception swallowing forbidden."""
    with pytest.raises(OutputUnknownError, match="bogus_output"):
        apply_mandatory_outputs(
            snapshot=coyote_snapshot,
            outputs=["bogus_output"],
            actor="sira_mendes",
        )


def test_multiple_outputs_all_apply(coyote_snapshot) -> None:
    """Each ID in ``outputs`` is applied — none silently skipped."""
    from sidequest.magic.state import BarKey

    apply_mandatory_outputs(
        snapshot=coyote_snapshot,
        outputs=["sanity_decrement", "sanity_decrement"],
        actor="sira_mendes",
    )
    sanity = coyote_snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    )
    # Two 0.10 debits.
    assert sanity.value == pytest.approx(0.80)


def test_status_add_wound_records_promotion(coyote_snapshot) -> None:
    """status_add_wound surfaces somewhere on the character/snapshot.

    The exact storage location is a green-phase choice (Status list on
    GameSnapshot, MagicState side-channel, etc.). The contract this test
    asserts is: ``apply_mandatory_outputs(["status_add_wound"], ...)``
    does *not* silently no-op — observable state changes.
    """
    pre_state = coyote_snapshot.magic_state.model_copy(deep=True)

    apply_mandatory_outputs(
        snapshot=coyote_snapshot,
        outputs=["status_add_wound"],
        actor="sira_mendes",
        status_text="Bleeding through",
    )

    # The handler must change *something* observable. We don't pin the
    # exact field — just enforce that the call has an effect.
    post_state = coyote_snapshot.magic_state
    assert post_state.model_dump() != pre_state.model_dump(), (
        "status_add_wound must record an observable state change "
        "(no silent no-op)"
    )


def test_control_tier_advance_records_increment(coyote_snapshot) -> None:
    """control_tier_advance bumps the actor's innate control tier.

    Storage location is implementer's choice (per plan §5.4: likely on
    MagicState as a per-(actor, plugin) tier dict). Regardless, the
    pre/post state must differ — not a silent no-op.
    """
    pre_state = coyote_snapshot.magic_state.model_copy(deep=True)

    apply_mandatory_outputs(
        snapshot=coyote_snapshot,
        outputs=["control_tier_advance"],
        actor="sira_mendes",
    )

    assert coyote_snapshot.magic_state.model_dump() != pre_state.model_dump(), (
        "control_tier_advance must record an observable state change"
    )


def test_world_scope_output_uses_world_owner(coyote_snapshot) -> None:
    """``hegemony_heat_increment`` mutates the world-scope bar, not character-scope."""
    from sidequest.magic.state import BarKey

    pre_value = coyote_snapshot.magic_state.get_bar(
        BarKey(scope="world", owner_id="coyote_star", bar_id="hegemony_heat")
    ).value

    apply_mandatory_outputs(
        snapshot=coyote_snapshot,
        outputs=["hegemony_heat_increment"],
        actor="sira_mendes",
    )

    post_value = coyote_snapshot.magic_state.get_bar(
        BarKey(scope="world", owner_id="coyote_star", bar_id="hegemony_heat")
    ).value
    assert post_value > pre_value


def test_emits_otel_span_per_output(
    coyote_snapshot, captured_watcher_events: list[dict[str, Any]]
) -> None:
    """OTEL Observability Principle: every output emission emits a watcher event.

    The GM panel is the lie detector. Without spans we can't tell
    whether the dispatcher engaged or whether nothing happened.
    """
    apply_mandatory_outputs(
        snapshot=coyote_snapshot,
        outputs=["sanity_decrement"],
        actor="sira_mendes",
    )

    magic_events = [
        e
        for e in captured_watcher_events
        if e["component"] == "magic"
        and e["fields"].get("op") in {"confrontation_outcome", "mandatory_output"}
    ]
    assert magic_events, (
        "expected a magic-component watcher event with op="
        "confrontation_outcome or mandatory_output; "
        f"saw events: {[(e['component'], e['fields'].get('op')) for e in captured_watcher_events]}"
    )
    fields = magic_events[0]["fields"]
    assert fields.get("output_id") == "sanity_decrement"
    assert fields.get("actor") == "sira_mendes"
