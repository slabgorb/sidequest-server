"""End-to-end: connect → chargen → first turn lands the authored setting.

This test would have caught the live Mendes' Post bug. After chargen
completes, ``state.location_update`` should match the chosen Opening's
``setting.interior_room`` (chassis-anchored) or ``location_label``,
NOT a narrator-invented place.

See ``docs/superpowers/specs/2026-05-01-canned-openings-design.md`` §7.3.
"""

from __future__ import annotations

import os

import pytest

# This is an e2e flow test — relies on the full server pipeline and a
# live Claude subprocess for the first narrator turn. Skip unless
# explicitly opted in (matches existing e2e patterns).
if not os.environ.get("SIDEQUEST_E2E_OPENINGS"):
    pytest.skip(
        "End-to-end opening test — set SIDEQUEST_E2E_OPENINGS=1 to run "
        "against a live server pipeline.",
        allow_module_level=True,
    )


def test_coyote_star_solo_first_turn_lands_in_galley() -> None:
    """Connect a fresh solo session to coyote_star, run chargen with the
    'Far Landing Raised Me' background, fire turn 1, assert the resulting
    state.location includes 'Galley' or 'Kestrel' — NOT 'Mendes' Post' or
    'New Claim' (the prior narrator-improvised regression).

    The actual e2e harness depends on the test infrastructure pattern in
    ``tests/e2e/test_server_e2e.py``. After this scaffold is lifted,
    follow that pattern: spawn server, connect WS, run chargen flow,
    assert on ``state.location_update`` from the first narrator turn.
    """
    pytest.skip(
        "Implementation depends on the existing e2e harness. Follow "
        "tests/e2e/test_server_e2e.py: spawn server, connect WS, run "
        "chargen flow, assert state.location_update from the first "
        "narrator turn references a Kestrel interior room (galley / "
        "cockpit / engineering / deck_three_corridor)."
    )
