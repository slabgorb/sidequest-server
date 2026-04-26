"""Unit tests for the ImagePacingThrottle (ADR-050).

Covers the pure throttle state machine: defaults, cooldown semantics,
force override, mid-session reconfiguration, and per-instance isolation
between sessions. Wiring tests against the dispatch path live in
``test_render_dispatch_throttle.py``.
"""

from __future__ import annotations

import pytest

from sidequest.server.image_pacing import (
    DEFAULT_MULTIPLAYER_COOLDOWN_SECONDS,
    DEFAULT_SOLO_COOLDOWN_SECONDS,
    ImagePacingThrottle,
)


def test_defaults_match_adr_050() -> None:
    assert DEFAULT_SOLO_COOLDOWN_SECONDS == 30
    assert DEFAULT_MULTIPLAYER_COOLDOWN_SECONDS == 60


def test_for_solo_constructs_30_second_throttle() -> None:
    t = ImagePacingThrottle.for_solo()
    assert t.cooldown_seconds == 30


def test_for_multiplayer_constructs_60_second_throttle() -> None:
    t = ImagePacingThrottle.for_multiplayer()
    assert t.cooldown_seconds == 60


@pytest.mark.parametrize(
    ("player_count", "expected"),
    [(0, 30), (1, 30), (2, 60), (5, 60)],
)
def test_default_for_player_count(player_count: int, expected: int) -> None:
    t = ImagePacingThrottle.default_for_player_count(player_count)
    assert t.cooldown_seconds == expected


def test_first_render_always_allowed() -> None:
    t = ImagePacingThrottle.for_solo()
    decision = t.should_render(now=100.0)
    assert decision.allowed is True
    assert decision.reason == "first_render"
    assert decision.cooldown_remaining_seconds == 0


def test_should_render_false_within_cooldown() -> None:
    t = ImagePacingThrottle.for_solo()  # 30s
    t.record_render(now=100.0)
    decision = t.should_render(now=120.0)  # 20s elapsed
    assert decision.allowed is False
    assert decision.reason == "cooldown_active"
    assert decision.cooldown_remaining_seconds == 10


def test_should_render_true_after_cooldown() -> None:
    t = ImagePacingThrottle.for_solo()  # 30s
    t.record_render(now=100.0)
    decision = t.should_render(now=130.0)  # exactly 30s elapsed
    assert decision.allowed is True
    assert decision.reason == "cooldown_elapsed"


def test_should_render_true_well_past_cooldown() -> None:
    t = ImagePacingThrottle.for_multiplayer()  # 60s
    t.record_render(now=100.0)
    decision = t.should_render(now=500.0)
    assert decision.allowed is True
    assert decision.reason == "cooldown_elapsed"


def test_zero_cooldown_disables_throttle() -> None:
    t = ImagePacingThrottle(cooldown_seconds=0)
    t.record_render(now=100.0)
    decision = t.should_render(now=100.001)
    assert decision.allowed is True
    assert decision.reason == "throttle_disabled"


def test_should_render_is_pure_does_not_advance_state() -> None:
    """Calling should_render must not mutate the throttle — only
    record_render should."""
    t = ImagePacingThrottle.for_solo()
    t.record_render(now=100.0)
    snapshot_before = t.last_render_monotonic
    for tick in (105.0, 110.0, 115.0):
        t.should_render(now=tick)
    assert t.last_render_monotonic == snapshot_before


def test_force_render_does_not_reset_timer() -> None:
    """ADR-050 explicit semantic: GM force-override does NOT reset the
    cooldown. The next organic render still has to wait out the original
    window."""
    t = ImagePacingThrottle.for_solo()  # 30s
    t.record_render(now=100.0)
    forced = t.force_render()
    assert forced.allowed is True
    assert forced.reason == "forced"

    # The organic dispatch is still throttled — force_render must not
    # have updated last_render_monotonic.
    decision = t.should_render(now=110.0)
    assert decision.allowed is False
    assert decision.cooldown_remaining_seconds == 20


def test_record_render_resets_window() -> None:
    t = ImagePacingThrottle.for_solo()
    t.record_render(now=100.0)
    # Past cooldown — allowed.
    assert t.should_render(now=140.0).allowed is True
    # Recording a new render restarts the cooldown from now.
    t.record_render(now=140.0)
    assert t.should_render(now=145.0).allowed is False


def test_set_cooldown_seconds_changes_window() -> None:
    t = ImagePacingThrottle.for_solo()  # 30s
    t.record_render(now=100.0)
    assert t.should_render(now=110.0).allowed is False
    # Shorten cooldown to 5s — already past it.
    t.set_cooldown_seconds(5)
    assert t.should_render(now=110.0).allowed is True


def test_set_cooldown_seconds_rejects_negative() -> None:
    t = ImagePacingThrottle.for_solo()
    with pytest.raises(ValueError):
        t.set_cooldown_seconds(-1)


def test_per_instance_state_two_throttles_independent() -> None:
    """Multi-session test: two throttles must NOT share state. Each
    WebSocket session owns its own throttle on _SessionData; a render in
    session A must not affect session B's cooldown."""
    a = ImagePacingThrottle.for_solo()
    b = ImagePacingThrottle.for_solo()
    a.record_render(now=100.0)
    # Session A is throttled; session B's first render must still fire.
    assert a.should_render(now=110.0).allowed is False
    assert b.should_render(now=110.0).allowed is True
    assert b.should_render(now=110.0).reason == "first_render"
