import json

import pytest

from sidequest.game.persistence import SqliteStore
from sidequest.telemetry.watcher_hub import bind_event_store, publish_event


@pytest.fixture(autouse=True)
def _clear_event_store_binding():
    """Guarantee the process-global _event_store binding is cleared after
    every test, even if a test raises before its own finally — keeps the
    tests in this module order-independent as more are appended."""
    yield
    bind_event_store(None)


def _store(tmp_path) -> SqliteStore:
    return SqliteStore.open(str(tmp_path / "save.db"))


def test_publish_outside_txn_writes_row_with_null_event_seq(tmp_path):
    store = _store(tmp_path)
    try:
        bind_event_store(store)
        publish_event(
            "state_transition",
            {"field": "intent", "label": "explore", "round": 3},
            component="intent",
        )
        rows = store._conn.execute(
            "SELECT event_seq, round, component, event_type, payload_json FROM turn_telemetry"
        ).fetchall()
        assert len(rows) == 1
        event_seq, rnd, component, event_type, payload = rows[0]
        assert event_seq is None  # fired outside any turn (C2) transaction
        assert rnd == 3  # best-effort from fields["round"]
        assert component == "intent"
        assert event_type == "state_transition"
        assert json.loads(payload) == {
            "field": "intent",
            "label": "explore",
            "round": 3,
        }
    finally:
        bind_event_store(None)
        store.close()


def test_publish_inside_open_txn_joins_it_and_attributes_event_seq(tmp_path):
    """When a C2 turn transaction is open, the sink must NOT commit and must
    attribute event_seq = the in-flight events row. Atomicity: rolling back
    the turn rolls back the telemetry too."""
    store = _store(tmp_path)
    try:
        bind_event_store(store)
        conn = store._conn
        try:
            with conn:
                conn.execute(
                    "INSERT INTO events (kind, payload_json, created_at) "
                    "VALUES ('NARRATION', '{}', 't')"
                )
                assert conn.in_transaction is True
                publish_event(
                    "state_transition",
                    {"field": "projection", "decision": "include"},
                    component="projection",
                )
                inflight = conn.execute("SELECT event_seq FROM turn_telemetry").fetchall()
                assert (
                    len(inflight) == 1 and inflight[0][0] == 1
                )  # = MAX(seq) of the in-flight event
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        assert conn.execute("SELECT COUNT(*) FROM turn_telemetry").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        bind_event_store(None)
        store.close()


def test_no_store_bound_is_noop_not_error(tmp_path):
    bind_event_store(None)  # legacy/in-memory session: no durable save
    publish_event("x", {"a": 1}, component="c")  # must not raise


def test_round_absent_or_non_int_is_stored_null(tmp_path):
    store = _store(tmp_path)
    try:
        bind_event_store(store)
        publish_event("e", {"no_round_here": True}, component="c")
        publish_event("e", {"round": "not-an-int"}, component="c")
        rounds = [
            r[0]
            for r in store._conn.execute("SELECT round FROM turn_telemetry ORDER BY seq").fetchall()
        ]
        assert rounds == [None, None]
    finally:
        bind_event_store(None)
        store.close()
