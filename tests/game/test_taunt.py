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


# ---------------------------------------------------------------------------
# Task 6: taunt decay + OTEL expiration on round-advance hook
# ---------------------------------------------------------------------------


def test_taunt_expires_at_end_of_round_emits_otel(taunt_test_encounter, otel_capture):
    """When the round advances after taunt is active, tick_taunt_round_advance()
    clears the taunter and emits encounter.taunt.expired with the prior
    actor_id and the round that just ended.

    The round counter used here is TurnManager.round — the same counter
    that record_interaction() increments on every player-narrator exchange.
    The production hook calls tick_taunt_round_advance() right after
    record_interaction() in _execute_narration_turn.
    """
    from sidequest.game.taunt_tick import tick_taunt_round_advance
    from sidequest.game.turn import TurnManager

    helper = taunt_test_encounter
    enc = helper.enc
    fighter_id = helper.fighter_id

    # Activate taunt.
    enc.taunt.activate(actor_id=fighter_id)
    assert enc.taunt.active_actor == fighter_id

    # Simulate TurnManager at a known round.
    tm = TurnManager(round=5)
    round_before = tm.round  # 5 — the round that is about to end

    # Advance the round (mirrors record_interaction()'s increment).
    tm.record_interaction()

    # Now call the production hook: decay + conditional OTEL.
    tick_taunt_round_advance(enc, prior_round=round_before)

    # Taunt must be cleared.
    assert enc.taunt.active_actor is None

    # Exactly one expiry span must have fired.
    events = [s for s in otel_capture.get_finished_spans() if s.name == "encounter.taunt.expired"]
    assert len(events) == 1, (
        f"Expected 1 expired event, got {len(events)}: "
        f"{[s.name for s in otel_capture.get_finished_spans()]}"
    )
    assert events[0].attributes["actor_id"] == fighter_id
    assert events[0].attributes["round"] == round_before  # the round that just ended


def test_taunt_no_expiry_event_when_inactive(taunt_test_encounter, otel_capture):
    """tick_taunt_round_advance is a no-op (no span) when taunt is not active."""
    from sidequest.game.taunt_tick import tick_taunt_round_advance

    enc = taunt_test_encounter.enc
    assert enc.taunt.active_actor is None

    tick_taunt_round_advance(enc, prior_round=1)

    events = [s for s in otel_capture.get_finished_spans() if s.name == "encounter.taunt.expired"]
    assert len(events) == 0
