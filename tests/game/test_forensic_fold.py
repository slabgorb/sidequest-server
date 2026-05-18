import json

from sidequest.game.event_log import EventRow
from sidequest.game.forensic_fold import (
    STATE_DELTA_FIELDS,
    FoldResult,
    fold_state_deltas,
)


def _ev(seq: int, payload: dict, kind: str = "NARRATION") -> EventRow:
    return EventRow(seq=seq, kind=kind, payload_json=json.dumps(payload), created_at="t")


def test_empty_event_list_yields_empty_result():
    result = fold_state_deltas([])
    assert result == FoldResult(derived={}, unparseable_seqs=())


def test_events_without_state_delta_contribute_nothing():
    events = [
        _ev(1, {"type": "NARRATION", "text": "hello"}),
        _ev(2, {"type": "NARRATION", "state_delta": None}),
    ]
    result = fold_state_deltas(events)
    assert result.derived == {}
    assert result.unparseable_seqs == ()


def test_valid_json_non_dict_payload_is_recorded_loudly_not_dropped(caplog):
    events = [
        EventRow(seq=3, kind="NARRATION", payload_json="null", created_at="t"),
        _ev(4, {"type": "NARRATION", "state_delta": {"location": "Cave"}}),
    ]
    with caplog.at_level("WARNING"):
        result = fold_state_deltas(events)
    assert result.unparseable_seqs == (3,)
    assert result.derived["location"].value == "Cave"  # good event still folds
    assert "forensic_fold.non_dict_payload seq=3" in caplog.text


def test_state_delta_fields_match_protocol_model():
    from sidequest.protocol.models import StateDelta

    assert set(STATE_DELTA_FIELDS) == set(StateDelta.model_fields)


def test_last_write_wins_with_ordered_provenance():
    events = [
        _ev(5, {"type": "NARRATION", "state_delta": {"location": "Cave"}}),
        _ev(2, {"type": "NARRATION", "state_delta": {"location": "Gate"}}),
        _ev(9, {"state_delta": {"location": "Hall"}}, kind="TURN_STATUS"),
    ]
    result = fold_state_deltas(events)
    loc = result.derived["location"]
    assert loc.value == "Hall"  # highest seq wins (sorted internally)
    assert loc.source_seqs == (2, 5, 9)  # every contributing seq, in order


def test_independent_fields_tracked_separately():
    events = [
        _ev(1, {"type": "NARRATION", "state_delta": {"location": "Cave"}}),
        _ev(2, {"type": "NARRATION", "state_delta": {"quests": {"q1": "open"}}}),
    ]
    result = fold_state_deltas(events)
    assert result.derived["location"].source_seqs == (1,)
    assert result.derived["quests"].value == {"q1": "open"}
    assert result.derived["quests"].source_seqs == (2,)
    assert "characters" not in result.derived  # absent, not fabricated
