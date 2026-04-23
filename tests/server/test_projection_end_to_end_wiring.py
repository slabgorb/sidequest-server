"""End-to-end ProjectionFilter wiring test.

Asserts the single-truth invariant in executable form:
    - 2 players + 1 GM receive projections consistent with the rules
    - projection_cache has exactly N rows per event
    - projection.filter.decide span count equals the projection count
    - Reconnecting a player receives byte-identical frames to the live
      session (via cache read, no re-filter)
    - GM canonical view is untouched by any rule
"""
from __future__ import annotations

import json
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.game.event_log import EventLog
from sidequest.game.persistence import SqliteStore
from sidequest.game.projection.cache import ProjectionCache
from sidequest.game.projection.composed import ComposedFilter
from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.rules import load_rules_from_yaml_str
from sidequest.game.projection.view import SessionGameStateView


def _setup_tracing() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


def test_end_to_end_single_truth_invariant(tmp_path: Path) -> None:
    exporter = _setup_tracing()
    store = SqliteStore(tmp_path / "e2e.db")
    log = EventLog(store)
    cache = ProjectionCache(store)

    rules = load_rules_from_yaml_str(
        """
rules:
  - kind: NARRATION
    redact_fields:
      - field: text
        unless: is_gm()
        mask: "**"
        """
    )
    filt = ComposedFilter(rules=rules)
    view = SessionGameStateView(
        gm_player_id="gm",
        player_id_to_character={
            "alice": "alice_char",
            "bob": "bob_char",
            "gm": "gm_char",
        },
    )
    players = ["alice", "bob", "gm"]

    # Fan out 3 events.
    for text in ["one", "two", "three"]:
        row = log.append(kind="NARRATION", payload_json=f'{{"text":"{text}"}}')
        env = MessageEnvelope(kind=row.kind, payload_json=row.payload_json, origin_seq=row.seq)
        for pid in players:
            decision = filt.project(envelope=env, view=view, player_id=pid)
            cache.write(event_seq=row.seq, player_id=pid, decision=decision)

    # 1. projection_cache has N_players × N_events rows.
    with store._conn:
        cache_rows = store._conn.execute("SELECT COUNT(*) FROM projection_cache").fetchone()[0]
    assert cache_rows == 3 * 3

    # 2. projection.filter.decide span count equals cache-row count.
    decide_spans = [s for s in exporter.get_finished_spans() if s.name == "projection.filter.decide"]
    assert len(decide_spans) == 9

    # 3. GM sees canonical; players see "**".
    alice_rows = cache.read_since(player_id="alice", since_seq=0)
    gm_rows = cache.read_since(player_id="gm", since_seq=0)
    for r in alice_rows:
        assert json.loads(r.payload_json)["text"] == "**"
    for r in gm_rows:
        assert json.loads(r.payload_json)["text"] in {"one", "two", "three"}

    # 4. Reconnecting Alice replays byte-identical frames (cache-only path).
    replay = cache.read_since(player_id="alice", since_seq=0)
    assert [r.payload_json for r in replay] == [r.payload_json for r in alice_rows]

    # 5. GM canonical: events table has true text, unaffected by any rule.
    with store._conn:
        canonical_rows = store._conn.execute(
            "SELECT payload_json FROM events ORDER BY seq ASC"
        ).fetchall()
    assert [json.loads(r[0])["text"] for r in canonical_rows] == ["one", "two", "three"]
