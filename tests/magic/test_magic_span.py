"""magic.working OTEL span emits via watcher_hub when working applied.

Adapted from the plan (lines 4471-4549). The plan's draft used a
``watcher_hub.subscribe(callback)`` shape that doesn't exist on the
actual hub (it has async ``subscribe(_Sendable)`` + sync ``publish``);
we monkeypatch ``narration_apply._watcher_publish`` to capture, the
same pattern ``tests/server/test_confrontation_pc_consent_gate.py``
established for unit-level watcher inspection.

The route at ``SPAN_ROUTES['magic.working']`` extracts the same fields
the dashboard renders (component=magic, event_type=state_transition,
op=working). The OTEL span fan-out is covered by the routing-
completeness test; here we assert the *direct* publish that happens
alongside the span open, so the GM panel's event feed sees the
working without depending on a tracer provider being installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from sidequest.magic.models import HardLimit, WorldMagicConfig


@pytest.fixture()
def coyote_world_config(world_config: WorldMagicConfig) -> WorldMagicConfig:
    """Conftest world_config + ``no_resurrection`` hard limit.

    The DEEP_RED test needs a hard limit whose id matches the
    "resurrection" keyword the validator scans for; the conftest's
    ``psionics_never_decisive`` limit alone won't trip on the
    "resurrection of the dead pilot" basis. Same shape as
    ``test_narration_apply_magic.py`` and ``test_threshold_promotion.py``.
    """
    augmented = list(world_config.hard_limits) + [
        HardLimit(id="no_resurrection", description="death is permanent"),
    ]
    return world_config.model_copy(update={"hard_limits": augmented})


@pytest.fixture
def coyote_snapshot(coyote_world_config: WorldMagicConfig):
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import MagicState

    state = MagicState.from_config(coyote_world_config)
    state.add_character("sira_mendes")
    return GameSnapshot.model_construct(magic_state=state)


@pytest.fixture
def captured_watcher_events(monkeypatch) -> Iterator[list[dict[str, Any]]]:
    """Intercept ``narration_apply._watcher_publish`` calls.

    Mirror of ``tests/server/test_confrontation_pc_consent_gate.py``
    fixture: lets us assert on the published payload without binding
    a real asyncio loop or constructing a fake WebSocket subscriber.
    """
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


def test_span_route_registered():
    """The star-import side effect must populate ``SPAN_ROUTES``.

    Wiring test (CLAUDE.md): without this, a future regression that
    drops the star-import from ``spans/__init__.py`` would leave the
    span constant defined but un-routed — and the routing-completeness
    test alone wouldn't catch the missing dashboard wiring.
    """
    from sidequest.telemetry.spans._core import SPAN_ROUTES

    assert "magic.working" in SPAN_ROUTES
    route = SPAN_ROUTES["magic.working"]
    assert route.event_type == "state_transition"
    assert route.component == "magic"


def test_apply_magic_working_emits_span(coyote_snapshot, captured_watcher_events):
    """Calling apply_magic_working publishes a ``state_transition`` event
    with ``component=magic`` and ``op=working`` — clean working, no flags."""
    from sidequest.server.narration_apply import apply_magic_working

    apply_magic_working(
        snapshot=coyote_snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.12},
            "domain": "psychic",
            "narrator_basis": "x",
            "flavor": "acquired",
            "consent_state": "involuntary",
        },
    )

    matching = [
        e
        for e in captured_watcher_events
        if e["component"] == "magic"
        and e["event_type"] == "state_transition"
        and e["fields"].get("op") == "working"
    ]
    assert len(matching) == 1, (
        f"expected exactly one magic.working publish, got "
        f"{len(matching)}: {[e['fields'] for e in captured_watcher_events]}"
    )
    fields = matching[0]["fields"]
    assert fields["plugin"] == "innate_v1"
    assert fields["actor"] == "sira_mendes"
    assert fields["costs_debited"] == {"sanity": 0.12}
    assert "ledger_after" in fields
    assert fields["ledger_after"]["sanity"] == pytest.approx(0.88)
    assert fields["flags"] == []  # clean working


def test_deep_red_flag_appears_in_span(coyote_snapshot, captured_watcher_events):
    """Hard-limit violation surfaces in the span flags list.

    Adapted from plan lines 4515-4549 — the validator stamps the
    hard-limit-violation flag at DEEP_RED when ``narrator_basis`` trips
    a hard-limit keyword (``no_resurrection`` ↔ "resurrection of the
    dead pilot"). Span carries that flag through so the GM panel /
    dashboard event feed can highlight the violation.
    """
    from sidequest.server.narration_apply import apply_magic_working

    apply_magic_working(
        snapshot=coyote_snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.5},
            "domain": "psychic",
            "narrator_basis": "resurrection of the dead pilot",
            "flavor": "acquired",
            "consent_state": "involuntary",
        },
    )

    matching = [
        e
        for e in captured_watcher_events
        if e["component"] == "magic"
        and e["event_type"] == "state_transition"
        and e["fields"].get("op") == "working"
    ]
    assert len(matching) == 1
    flags = matching[0]["fields"]["flags"]
    assert any(f["severity"] == "deep_red" for f in flags), (
        f"expected at least one deep_red flag, got: {flags}"
    )
