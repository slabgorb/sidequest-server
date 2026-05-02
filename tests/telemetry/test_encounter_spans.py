"""Encounter span sanity tests.

These don't try to assert the full OTEL export pipeline — they just confirm
the new span name constants exist with the documented strings, the context
managers can be entered/exited, and they accept the documented attributes.
The events-table persistence tests live in test_encounter_telemetry.py.
"""

from sidequest.telemetry import spans


def test_new_span_names():
    assert spans.SPAN_ENCOUNTER_BEAT_SKIPPED == "encounter.beat_skipped"
    assert spans.SPAN_ENCOUNTER_INVALID_SIDE == "encounter.invalid_side"
    assert spans.SPAN_ENCOUNTER_INVALID_OUTCOME_TIER == "encounter.invalid_outcome_tier"
    assert spans.SPAN_ENCOUNTER_METRIC_ADVANCE == "encounter.metric_advance"
    assert spans.SPAN_ENCOUNTER_TAG_CREATED == "encounter.tag_created"
    assert spans.SPAN_ENCOUNTER_TAG_BACKFIRE == "encounter.tag_backfire"
    assert spans.SPAN_ENCOUNTER_STATUS_ADDED == "encounter.status_added"
    assert spans.SPAN_ENCOUNTER_YIELD_RECEIVED == "encounter.yield_received"
    assert spans.SPAN_ENCOUNTER_YIELD_RESOLVED == "encounter.yield_resolved"
    assert spans.SPAN_ENCOUNTER_RESOLUTION_SIGNAL_EMITTED == "encounter.resolution_signal_emitted"
    assert spans.SPAN_ENCOUNTER_RESOLUTION_SIGNAL_CONSUMED == "encounter.resolution_signal_consumed"


def test_context_managers_smoke():
    with spans.encounter_beat_skipped_span(
        reason="neutral_actor",
        actor="Host",
        actor_side="neutral",
        beat_id="attack",
    ):
        pass
    with spans.encounter_invalid_side_span(
        actor_name="??",
        declared_side="enemy",
        valid_set="player|opponent|neutral",
    ):
        pass
    with spans.encounter_invalid_outcome_tier_span(
        beat_id="attack",
        actor="Sam",
        declared_tier="Wibble",
        valid_set="CritFail|Fail|Tie|Success|CritSuccess",
    ):
        pass
    with spans.encounter_metric_advance_span(
        side="player",
        delta_kind="own",
        delta=2,
        before=0,
        after=2,
    ):
        pass
    with spans.encounter_tag_created_span(
        tag_text="Off-Balance",
        created_by="Sam",
        target="Promo",
        leverage=1,
        fleeting=False,
        created_via="angle_beat",
    ):
        pass
    with spans.encounter_tag_backfire_span(
        tag_text="Off-Balance",
        created_by="Sam",
        target="Sam",
        triggering_beat="feint",
    ):
        pass
    with spans.encounter_status_added_span(
        actor="Sam",
        text="Cracked Temple",
        severity="Wound",
        source="narrator_extraction",
    ):
        pass
    with spans.encounter_yield_received_span(
        player_id="p1",
        actor_name="Sam",
        prior_player_metric=4,
        prior_opponent_metric=7,
        statuses_taken_this_encounter=1,
    ):
        pass
    with spans.encounter_yield_resolved_span(
        outcome="yielded",
        yielded_actors=("Sam",),
        edge_refreshed=2,
    ):
        pass
    with spans.encounter_resolution_signal_emitted_span(
        outcome="opponent_victory",
        final_player_metric=4,
        final_opponent_metric=11,
    ):
        pass
    with spans.encounter_resolution_signal_consumed_span(
        outcome="opponent_victory",
        final_player_metric=4,
        final_opponent_metric=11,
    ):
        pass
