import json

from sidequest.game.event_log import EventRow
from sidequest.game.forensic_fold import (
    FoldResult,
    MechanicalFold,
    TelemetryFold,
    fold_known_facts,
    fold_mechanical_census,
    fold_mechanical_strip,
    fold_turn_telemetry,
)


def _fn(fact_id: str, summary: str, category: str = "Lore", *, is_new: bool = True) -> dict:
    """One footnote/KnownFact, production shape (events.payload_json)."""
    return {"fact_id": fact_id, "summary": summary, "category": category, "is_new": is_new}


def _ev(seq: int, footnotes: list[dict] | None, kind: str = "NARRATION") -> EventRow:
    """A NARRATION event carrying ``footnotes`` — the real recorded shape.

    Production NARRATION payloads are ``{text, footnotes, _visibility}``;
    there is no ``state_delta`` key (verified against every real save
    2026-05-18). The fold reconstructs the KnownFacts ledger from footnotes.
    """
    payload: dict = {"text": "narration", "_visibility": {"visible_to": "all"}}
    if footnotes is not None:
        payload["footnotes"] = footnotes
    return EventRow(seq=seq, kind=kind, payload_json=json.dumps(payload), created_at="t")


def test_empty_event_list_yields_empty_result():
    result = fold_known_facts([])
    assert result == FoldResult(derived={}, unparseable_seqs=())


def test_events_without_footnotes_contribute_nothing():
    """SCRAPBOOK_ENTRY and footnote-less NARRATION carry no facts — not an error."""
    events = [
        _ev(1, None),  # NARRATION, no footnotes key
        _ev(2, []),  # NARRATION, empty footnotes
        EventRow(
            seq=3,
            kind="SCRAPBOOK_ENTRY",
            payload_json=json.dumps({"turn_id": 1, "location": "Cave"}),
            created_at="t",
        ),
    ]
    result = fold_known_facts(events)
    assert result.derived == {}
    assert result.unparseable_seqs == ()


def test_known_fact_is_reconstructed_keyed_by_fact_id():
    events = [_ev(1, [_fn("fn-aaa", "Turning Hub is a freeport station.", "Place")])]
    result = fold_known_facts(events)
    assert set(result.derived) == {"fn-aaa"}
    df = result.derived["fn-aaa"]
    assert df.value == {"summary": "Turning Hub is a freeport station.", "category": "Place"}
    assert df.source_seqs == (1,)


def test_valid_json_non_dict_payload_is_recorded_loudly_not_dropped(caplog):
    events = [
        EventRow(seq=3, kind="NARRATION", payload_json="null", created_at="t"),
        _ev(4, [_fn("fn-bbb", "The Kestrel is docked at Bay Three.", "Place")]),
    ]
    with caplog.at_level("WARNING"):
        result = fold_known_facts(events)
    assert result.unparseable_seqs == (3,)
    assert result.derived["fn-bbb"].value["summary"] == "The Kestrel is docked at Bay Three."
    assert "forensic_fold.non_dict_payload seq=3" in caplog.text


def test_restated_fact_accumulates_provenance_latest_summary_wins():
    """is_new=false re-assertions: every seq tracked, newest summary wins."""
    events = [
        _ev(5, [_fn("fn-ccc", "Suri Pell is a fixer.", "Person", is_new=True)]),
        _ev(2, [_fn("fn-ccc", "Suri Pell is a fixer.", "Person", is_new=True)]),
        _ev(
            9,
            [
                _fn(
                    "fn-ccc",
                    "Suri Pell is a fixer who owes Ritali a favor.",
                    "Person",
                    is_new=False,
                )
            ],
        ),
    ]
    result = fold_known_facts(events)
    fact = result.derived["fn-ccc"]
    assert fact.value["summary"] == "Suri Pell is a fixer who owes Ritali a favor."  # highest seq
    assert fact.source_seqs == (2, 5, 9)  # every contributing seq, sorted


def test_independent_facts_tracked_separately():
    events = [
        _ev(1, [_fn("fn-loc", "They are at the Docking Crescent.", "Place")]),
        _ev(2, [_fn("fn-quest", "Catalina wants the footage released.", "Quest")]),
    ]
    result = fold_known_facts(events)
    assert result.derived["fn-loc"].source_seqs == (1,)
    assert result.derived["fn-quest"].value["category"] == "Quest"
    assert result.derived["fn-quest"].source_seqs == (2,)
    assert "fn-missing" not in result.derived  # absent, not fabricated


def test_multiple_facts_in_one_event_all_fold():
    events = [
        _ev(
            1,
            [
                _fn("fn-1", "Turning Hub has a customs queue.", "Place"),
                _fn("fn-2", "Red Prospect is a gas giant.", "Lore"),
                _fn("fn-3", "Clan Moana-Teru runs the Hub.", "Person"),
            ],
        )
    ]
    result = fold_known_facts(events)
    assert set(result.derived) == {"fn-1", "fn-2", "fn-3"}
    assert all(df.source_seqs == (1,) for df in result.derived.values())


def test_malformed_footnote_skipped_loudly_siblings_still_fold(caplog):
    """A footnote with no fact_id has no stable identity — skip it loudly,
    but a good footnote in the same event still folds (No-Silent-Fallbacks:
    log, don't poison the whole event)."""
    events = [
        _ev(
            1,
            [
                {"summary": "no fact_id here", "category": "Lore"},  # malformed
                _fn("fn-ok", "This one is well-formed.", "Lore"),
            ],
        )
    ]
    with caplog.at_level("WARNING"):
        result = fold_known_facts(events)
    assert set(result.derived) == {"fn-ok"}
    assert "forensic_fold.malformed_footnote seq=1" in caplog.text


def test_unparseable_payload_is_recorded_and_logged_not_silently_dropped(caplog):
    bad = EventRow(seq=7, kind="NARRATION", payload_json="{not json", created_at="t")
    good = _ev(8, [_fn("fn-ddd", "The relay buoy routes anonymized headers.", "Lore")])
    with caplog.at_level("WARNING"):
        result = fold_known_facts([bad, good])
    assert result.unparseable_seqs == (7,)
    assert result.derived["fn-ddd"].value["summary"] == "The relay buoy routes anonymized headers."
    assert "forensic_fold.unparseable_payload seq=7" in caplog.text


def _trow(seq, component, event_type, payload_json, ts="t"):
    # mirrors the sqlite3.Row-ish dict the read path passes the fold
    return {
        "seq": seq,
        "component": component,
        "event_type": event_type,
        "ts": ts,
        "payload_json": payload_json,
    }


def test_telemetry_empty_yields_empty():
    result = fold_turn_telemetry([])
    assert result == TelemetryFold(rows=(), by_component={}, total=0, unparseable_seqs=())


def test_telemetry_groups_by_component_then_event_type_and_counts():
    rows = [
        _trow(1, "intent", "state_transition", '{"label":"explore"}'),
        _trow(2, "intent", "state_transition", '{"label":"talk"}'),
        _trow(3, "projection", "decision", '{"include":true}'),
    ]
    r = fold_turn_telemetry(rows)
    assert r.total == 3
    assert r.by_component == {
        "intent": {"state_transition": 2},
        "projection": {"decision": 1},
    }
    assert [(x.seq, x.component, x.event_type) for x in r.rows] == [
        (1, "intent", "state_transition"),
        (2, "intent", "state_transition"),
        (3, "projection", "decision"),
    ]
    assert r.rows[0].fields == {"label": "explore"}


def test_telemetry_rows_sorted_by_seq_regardless_of_input_order():
    rows = [
        _trow(3, "c", "e", "{}"),
        _trow(1, "c", "e", "{}"),
        _trow(2, "c", "e", "{}"),
    ]
    r = fold_turn_telemetry(rows)
    assert [x.seq for x in r.rows] == [1, 2, 3]


def test_telemetry_unparseable_payload_is_recorded_and_logged_not_dropped(caplog):
    bad = _trow(7, "c", "e", "{not json")
    good = _trow(8, "c", "e", '{"ok":1}')
    with caplog.at_level("WARNING"):
        r = fold_turn_telemetry([bad, good])
    assert r.unparseable_seqs == (7,)
    assert [x.seq for x in r.rows] == [8]  # good row still folds
    assert r.total == 1
    assert "forensic_fold.telemetry_unparseable_payload seq=7" in caplog.text


def test_telemetry_non_dict_payload_is_recorded_and_logged(caplog):
    bad = _trow(9, "c", "e", "[1,2,3]")  # valid JSON, not a dict
    with caplog.at_level("WARNING"):
        r = fold_turn_telemetry([bad])
    assert r.unparseable_seqs == (9,)
    assert r.rows == ()
    assert "forensic_fold.telemetry_non_dict_payload seq=9" in caplog.text


def test_telemetry_fold_never_raises_on_garbage_row_shape():
    # missing keys / None payload must not crash (defensive, like fold_known_facts)
    r = fold_turn_telemetry([{"seq": 1, "payload_json": None}])
    assert r.unparseable_seqs == (1,)
    assert r.rows == ()


def test_telemetry_seqless_row_is_skipped_loudly_not_recorded_not_raised(caplog):
    # A row with no usable int seq cannot be recorded in unparseable_seqs
    # (tuple[int,...]); it must be loud-logged, skipped, and never raise.
    with caplog.at_level("WARNING"):
        r = fold_turn_telemetry([{"payload_json": "{}"}, {}])
    assert r.rows == ()
    assert r.total == 0
    assert r.unparseable_seqs == ()  # nothing with an int seq to record
    assert "forensic_fold.telemetry_unparseable_payload seq=None" in caplog.text


def test_telemetry_none_component_buckets_under_empty_string():
    r = fold_turn_telemetry([_trow(1, None, "e", "{}")])
    assert r.total == 1
    assert r.by_component == {"": {"e": 1}}
    assert r.rows[0].component == ""


# ---- Phase 2: mechanical census fold tests ----


def _crow(seq, payload: dict, event_type="census"):
    return _trow(seq, "mechanical", event_type, json.dumps(payload))


def test_mechanical_empty_yields_absent():
    r = fold_mechanical_census([], [])
    assert isinstance(r, MechanicalFold)
    assert r.state == "absent"
    assert r.pcs == ()
    assert r.unparseable_seqs == ()


def test_mechanical_first_round_per_pc_is_baseline_no_deltas():
    cur = [_crow(1, {"player_id": "p1", "character_name": "Rux", "seat": 0,
                     "round": 1, "edge": {"current": 10, "max": 10},
                     "location": "Cave", "inventory": [{"item": "torch",
                     "qty": 1}], "xp": 0, "level": 1,
                     "acquired_advancements": []})]
    r = fold_mechanical_census(cur, [])  # no prior rows
    assert r.state == "moved"            # round has data
    [pc] = r.pcs
    assert pc.player_id == "p1"
    assert pc.kind == "baseline"         # first census -> absolute, no diff
    assert pc.deltas == ()
    assert pc.absolute["edge"] == {"current": 10, "max": 10}


def test_mechanical_no_change_is_static_not_moved():
    body = {"player_id": "p1", "character_name": "Rux", "seat": 0,
            "edge": {"current": 7, "max": 10}, "location": "Cave",
            "inventory": [{"item": "torch", "qty": 1}], "xp": 5,
            "level": 1, "acquired_advancements": []}
    prior = [_crow(1, {**body, "round": 1})]
    cur = [_crow(2, {**body, "round": 2})]
    r = fold_mechanical_census(cur, prior)
    [pc] = r.pcs
    assert pc.kind == "static"
    assert pc.deltas == ()
    assert r.state == "static"  # the WHOLE round had no mechanical change


def test_mechanical_moved_emits_typed_deltas():
    prior = [_crow(1, {"player_id": "p1", "character_name": "Rux", "seat": 0,
                       "round": 1, "edge": {"current": 10, "max": 10},
                       "location": "Ropefoot",
                       "inventory": [{"item": "torch", "qty": 1}], "xp": 0,
                       "level": 2, "acquired_advancements": []})]
    cur = [_crow(2, {"player_id": "p1", "character_name": "Rux", "seat": 0,
                     "round": 2, "edge": {"current": 7, "max": 10},
                     "location": "The Kept Fire",
                     "inventory": [{"item": "brass key", "qty": 1}],
                     "xp": 15, "level": 3,
                     "acquired_advancements": ["adv.iron_grip"]})]
    r = fold_mechanical_census(cur, prior)
    [pc] = r.pcs
    assert pc.kind == "moved"
    d = dict(pc.deltas)
    assert d["location"] == "Ropefoot → The Kept Fire"
    assert d["edge"] == "10→7 (−3)"
    assert d["xp"] == "+15"
    assert d["level"] == "2→3"
    assert d["inventory"] == "+brass key, −torch×1"
    assert d["advancements"] == "+adv.iron_grip"
    assert r.state == "moved"


def test_mechanical_trope_census_folds_session_block():
    prior = [_crow(1, {"round": 1, "active_tropes": [{"id": "vengeance",
             "status": "active", "progress": 0.2, "beats_fired": 1}],
             "turns_since_meaningful": 0, "total_beats_fired": 3},
             event_type="trope_census")]
    cur = [_crow(2, {"round": 2, "active_tropes": [{"id": "vengeance",
           "status": "active", "progress": 0.5, "beats_fired": 2}],
           "turns_since_meaningful": 1, "total_beats_fired": 4},
           event_type="trope_census")]
    r = fold_mechanical_census(cur, prior)
    assert r.trope is not None
    assert "vengeance" in r.trope["summary"]
    assert r.trope["kind"] == "moved"


def test_mechanical_unparseable_row_is_loud_skipped_and_recorded(caplog):
    bad = _trow(9, "mechanical", "census", "{not json")
    good = _crow(10, {"player_id": "p1", "character_name": "Rux",
                      "seat": 0, "round": 1, "edge": {"current": 1,
                      "max": 1}, "location": "Cave", "inventory": [],
                      "xp": 0, "level": 1, "acquired_advancements": []})
    with caplog.at_level("WARNING"):
        r = fold_mechanical_census([bad, good], [])
    assert r.unparseable_seqs == (9,)
    assert [pc.player_id for pc in r.pcs] == ["p1"]
    assert "forensic_fold.mechanical_unparseable_payload seq=9" in caplog.text


def test_fold_mechanical_strip_tristate_per_round():
    body = {"player_id": "p1", "character_name": "Rux", "seat": 0,
            "edge": {"current": 5, "max": 5}, "location": "Cave",
            "inventory": [], "xp": 0, "level": 1,
            "acquired_advancements": []}
    rows = [
        _crow(1, {**body, "round": 1}),                       # baseline
        _crow(2, {**body, "round": 2}),                       # static
        _crow(3, {**body, "round": 3, "xp": 9}),              # moved
    ]
    strip = fold_mechanical_strip(rows)
    assert strip == [
        {"round": 1, "state": "moved"},   # first census = has data
        {"round": 2, "state": "static"},
        {"round": 3, "state": "moved"},
    ]


def test_fold_mechanical_strip_empty_is_empty_list():
    assert fold_mechanical_strip([]) == []
