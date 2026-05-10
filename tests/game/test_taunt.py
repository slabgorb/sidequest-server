"""Story 2026-05-10 — taunt mechanic.

Activation, decay, OTEL emission. Targeting + redirect tested in
test_taunt_targeting.py.
"""
from __future__ import annotations

import pytest

from sidequest.game.taunt import TauntState


def test_taunt_state_starts_inactive():
    state = TauntState()
    assert state.active_actor is None
    assert state.remaining_rounds == 0
    assert state.redirects_this_round == 0


def test_taunt_activate_records_actor_and_round():
    state = TauntState()
    state.activate(actor_id="fighter-1")
    assert state.active_actor == "fighter-1"
    assert state.remaining_rounds == 1
    assert state.redirects_this_round == 0


def test_taunt_decay_at_end_of_round_clears_actor():
    state = TauntState()
    state.activate(actor_id="fighter-1")
    state.end_of_round_decay()
    assert state.active_actor is None
    assert state.remaining_rounds == 0


def test_taunt_redirect_count_resets_at_end_of_round():
    state = TauntState()
    state.activate(actor_id="fighter-1")
    state.try_consume_redirect()
    assert state.redirects_this_round == 1
    state.end_of_round_decay()
    state.activate(actor_id="fighter-1")
    assert state.redirects_this_round == 0


def test_taunt_redirect_capped_at_one_per_round():
    state = TauntState()
    state.activate(actor_id="fighter-1")
    assert state.try_consume_redirect() is True
    assert state.try_consume_redirect() is False  # second attempt rejected
    assert state.redirects_this_round == 1
