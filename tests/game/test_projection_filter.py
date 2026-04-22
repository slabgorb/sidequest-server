from sidequest.game.projection_filter import (
    ProjectionFilter,
    PassThroughFilter,
    FilterDecision,
)
from sidequest.game.event_log import EventRow


def _row(seq=1, kind="NARRATION", payload='{"text":"hi"}'):
    return EventRow(seq=seq, kind=kind, payload_json=payload, created_at="now")


def test_pass_through_includes_everything_for_everyone():
    f = PassThroughFilter()
    dec = f.project(event=_row(), player_id="alice")
    assert dec.include is True
    assert dec.payload_json == '{"text":"hi"}'


def test_filter_protocol_allows_redaction():
    class RedactHP(ProjectionFilter):
        def project(self, *, event, player_id):
            if event.kind == "STATE_UPDATE" and player_id != "gm":
                return FilterDecision(include=True, payload_json='{}')  # redacted
            return FilterDecision(include=True, payload_json=event.payload_json)

    f = RedactHP()
    dec = f.project(event=_row(kind="STATE_UPDATE", payload='{"hp":10}'), player_id="alice")
    assert dec.payload_json == '{}'
    dec_gm = f.project(event=_row(kind="STATE_UPDATE", payload='{"hp":10}'), player_id="gm")
    assert dec_gm.payload_json == '{"hp":10}'


def test_filter_can_omit():
    class OmitSecrets(ProjectionFilter):
        def project(self, *, event, player_id):
            if event.kind == "SECRET_NOTE" and player_id != "alice":
                return FilterDecision(include=False, payload_json="")
            return FilterDecision(include=True, payload_json=event.payload_json)

    f = OmitSecrets()
    assert f.project(event=_row(kind="SECRET_NOTE"), player_id="bob").include is False
    assert f.project(event=_row(kind="SECRET_NOTE"), player_id="alice").include is True
