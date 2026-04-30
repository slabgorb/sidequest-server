"""Phase 3 cut-point: end-to-end magic working from synthetic narrator output.

Three scripted scenarios:

1. Sira touches an alien panel — clean innate working, no flags, ledger
   1.0 → 0.88, no threshold crossings, magic.working span emitted.
2. Sira attempts resurrection — narrator violates the ``no_resurrection``
   hard_limit; ledger still updates (we don't interrupt narration in v1)
   but a DEEP_RED flag surfaces both in ``result.flags`` and in the
   emitted span's ``flags`` array.
3. Sanity threshold crossing promotes to a Status('Bleeding through',
   Wound) via ``promote_crossings_to_status_changes``.

Adapted from the plan's draft (lines 4729-4885). Two divergences from
the plan:

* watcher_hub: the plan's draft used
  ``watcher_hub.subscribe(callback) / unsubscribe(callback)`` — that
  callback shape doesn't exist on the actual hub. We monkeypatch
  ``narration_apply._watcher_publish`` instead, the canonical pattern
  established in ``tests/magic/test_magic_span.py``. The discriminator
  is ``event_type=='state_transition' and component=='magic' and
  fields['op']=='working'`` (not ``e['span']=='magic.working'``).
* world_config: the plan's ``_make_world_config_for_tests()`` helper
  doesn't exist. We use the conftest ``world_config`` fixture and
  ``model_copy`` in the resurrection-hard-limit, mirroring
  ``test_magic_span.py`` and ``test_narration_apply_magic.py``.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from sidequest.magic.models import HardLimit, WorldMagicConfig


@pytest.fixture()
def coyote_world_config(world_config: WorldMagicConfig) -> WorldMagicConfig:
    """Conftest world_config + ``no_resurrection`` hard limit.

    The DEEP_RED scenario needs a hard limit whose id matches the
    "resurrection" keyword the validator scans for.
    """
    augmented = list(world_config.hard_limits) + [
        HardLimit(id="no_resurrection", description="death is permanent"),
    ]
    return world_config.model_copy(update={"hard_limits": augmented})


@pytest.fixture
def captured_watcher_events(monkeypatch) -> Iterator[list[dict[str, Any]]]:
    """Intercept ``narration_apply._watcher_publish`` calls.

    Same shape as the fixture in ``tests/magic/test_magic_span.py``.
    """
    captured: list[dict[str, Any]] = []

    def _capture(event_type, fields, *, component="sidequest-server",
                 severity="info"):
        captured.append({
            "event_type": event_type,
            "fields": fields,
            "component": component,
            "severity": severity,
        })

    from sidequest.server import narration_apply
    monkeypatch.setattr(narration_apply, "_watcher_publish", _capture)
    yield captured


def test_sira_touches_alien_panel_clean_pass(
    world_config: WorldMagicConfig,
    captured_watcher_events: list[dict[str, Any]],
):
    """Sira touches the alien panel; reflexive psychic touch fires; no flags.

    Mirrors the worked example in
    docs/superpowers/specs/2026-04-28-magic-system-coyote-star-implementation-design.md §3.
    """
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.narration_apply import (
        apply_magic_working,
        promote_crossings_to_status_changes,
    )

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    snapshot = GameSnapshot.model_construct(magic_state=state)

    # === Synthetic narrator turn ===
    # (Prose is illustrative — the apply pipeline only consumes patch_field.)
    magic_working = {
        "plugin": "innate_v1",
        "mechanism": "condition",
        "actor": "sira_mendes",
        "costs": {"sanity": 0.12},
        "domain": "psychic",
        "narrator_basis": "alien-tech proximity triggers reflexive sympathetic-feel",
        "flavor": "acquired",
        "consent_state": "involuntary",
    }
    result = apply_magic_working(snapshot=snapshot, patch_field=magic_working)
    promotions = promote_crossings_to_status_changes(
        result=result, snapshot=snapshot
    )

    # === Validation ===
    # 1. No flags (clean working)
    assert result.flags == []

    # 2. Ledger updated correctly
    sanity = snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    )
    assert sanity.value == pytest.approx(0.88)

    # 3. No threshold crossings (sanity 1.0 → 0.88, threshold_low=0.40)
    assert result.crossings == []
    assert promotions == []

    # 4. Span emitted (via _watcher_publish — see module docstring for the
    #    plan-vs-reality adaptation).
    matching = [
        e for e in captured_watcher_events
        if e["component"] == "magic"
        and e["event_type"] == "state_transition"
        and e["fields"].get("op") == "working"
    ]
    assert len(matching) == 1
    fields = matching[0]["fields"]
    assert fields["plugin"] == "innate_v1"
    assert fields["flags"] == []
    assert fields["ledger_after"]["sanity"] == pytest.approx(0.88)

    # 5. Working logged
    assert len(snapshot.magic_state.working_log) == 1
    assert snapshot.magic_state.working_log[0].plugin == "innate_v1"


def test_sira_attempts_resurrection_deep_red_flag_surfaces(
    coyote_world_config: WorldMagicConfig,
    captured_watcher_events: list[dict[str, Any]],
):
    """Counter-example: narrator violates ``no_resurrection`` hard_limit.

    Ledger updates (we don't interrupt narration in v1); flag surfaces
    in ``result.flags`` AND in the emitted span's flags list.
    """
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import MagicState
    from sidequest.server.narration_apply import apply_magic_working

    state = MagicState.from_config(coyote_world_config)
    state.add_character("sira_mendes")
    snapshot = GameSnapshot.model_construct(magic_state=state)

    result = apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.30},
            "domain": "psychic",
            "narrator_basis": "psychic resurrection of the dead pilot",
            "flavor": "acquired",
            "consent_state": "involuntary",
        },
    )

    deep_red = [f for f in result.flags if f.severity.value == "deep_red"]
    assert len(deep_red) >= 1
    assert any("hard_limit" in f.reason for f in deep_red)

    matching = [
        e for e in captured_watcher_events
        if e["component"] == "magic"
        and e["event_type"] == "state_transition"
        and e["fields"].get("op") == "working"
    ]
    assert len(matching) == 1
    span_flags = matching[0]["fields"]["flags"]
    assert any(f["severity"] == "deep_red" for f in span_flags)


def test_sanity_crossing_promotes_status_change(world_config: WorldMagicConfig):
    """Cross sanity threshold → Status added via auto-promotion."""
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.narration_apply import (
        apply_magic_working,
        promote_crossings_to_status_changes,
    )

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.45
    )

    snapshot = GameSnapshot.model_construct(magic_state=state)
    result = apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.10},
            "domain": "psychic",
            "narrator_basis": "x",
            "flavor": "acquired",
            "consent_state": "involuntary",
        },
    )
    promotions = promote_crossings_to_status_changes(
        result=result, snapshot=snapshot
    )
    assert len(promotions) == 1
    assert promotions[0].status_text == "Bleeding through"
    assert promotions[0].severity == "Wound"
