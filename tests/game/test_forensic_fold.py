import json

from sidequest.game.event_log import EventRow
from sidequest.game.forensic_fold import (
    FoldResult,
    fold_known_facts,
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
