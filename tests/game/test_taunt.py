"""Story 2026-05-10 — taunt mechanic.

Activation, decay, OTEL emission. Targeting + redirect tested in
test_taunt_targeting.py.
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Task 3: beat-resolution wiring + OTEL
# ---------------------------------------------------------------------------


def test_taunt_beat_resolution_activates_state(taunt_test_encounter):
    """Resolving a 'taunt' beat for the Fighter sets active_actor and remaining_rounds."""
    enc = taunt_test_encounter.enc
    fighter_id = taunt_test_encounter.fighter_id

    taunt_test_encounter.resolve_beat(actor_id=fighter_id, beat_id="taunt", outcome="success")

    assert enc.taunt.active_actor == fighter_id
    assert enc.taunt.remaining_rounds == 1


def test_taunt_activation_emits_otel(taunt_test_encounter, otel_capture):
    enc = taunt_test_encounter.enc
    fighter_id = taunt_test_encounter.fighter_id

    taunt_test_encounter.resolve_beat(actor_id=fighter_id, beat_id="taunt", outcome="success")

    spans = [s for s in otel_capture.get_finished_spans() if s.name == "encounter.taunt.activated"]
    assert len(spans) == 1
    assert spans[0].attributes["actor_id"] == fighter_id
    assert spans[0].attributes["round"] == enc.beat
