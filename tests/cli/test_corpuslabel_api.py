from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.cli.corpuslabel.corpuslabel import build_app
from sidequest.corpus.schema import MineProvenance, TrainingPair
from sidequest.corpus.writer import write_pairs


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus.jsonl"
    labeled = tmp_path / "labeled.jsonl"
    pair = TrainingPair(
        schema_version=1,
        genre="g",
        world="w",
        round_number=1,
        input_text="hi",
        output_text="hello",
        provenance=MineProvenance(source_save="x.db", event_seq=None),
    )
    write_pairs(corpus, [pair])
    return corpus, labeled


def test_count_endpoint(tmp_path: Path) -> None:
    corpus, labeled = _seed(tmp_path)
    client = TestClient(build_app(corpus=corpus, labeled_out=labeled))
    resp = client.get("/api/count")
    assert resp.status_code == 200
    assert resp.json() == {"unlabeled": 1, "labeled": 0}


def test_pairs_endpoint_returns_pair(tmp_path: Path) -> None:
    corpus, labeled = _seed(tmp_path)
    client = TestClient(build_app(corpus=corpus, labeled_out=labeled))
    resp = client.get("/api/pairs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["input_text"] == "hi"
    assert data[0]["schema_version"] == 1


def test_label_endpoint_appends(tmp_path: Path) -> None:
    corpus, labeled = _seed(tmp_path)
    client = TestClient(build_app(corpus=corpus, labeled_out=labeled))
    resp = client.post(
        "/api/label",
        json={
            "pair": {
                "schema_version": 1,
                "genre": "g",
                "world": "w",
                "round_number": 1,
                "input_text": "hi",
                "output_text": "hello",
                "provenance": {"source_save": "x.db", "event_seq": None},
            },
            "disputes": ["mis_resolved_referent"],
            "corrected_output": "The NPC is not present.",
            "labeler": "keith",
        },
    )
    assert resp.status_code == 200
    assert labeled.exists()
    line = labeled.read_text().strip()
    record = json.loads(line)
    assert record["labeler"] == "keith"
    assert record["disputes"] == ["mis_resolved_referent"]


def test_label_endpoint_appends_multiple(tmp_path: Path) -> None:
    corpus, labeled = _seed(tmp_path)
    client = TestClient(build_app(corpus=corpus, labeled_out=labeled))
    body = {
        "pair": {
            "schema_version": 1,
            "genre": "g",
            "world": "w",
            "round_number": 1,
            "input_text": "hi",
            "output_text": "hello",
            "provenance": {"source_save": "x.db", "event_seq": None},
        },
        "disputes": [],
        "corrected_output": "ok",
        "labeler": "keith",
    }
    client.post("/api/label", json=body)
    client.post("/api/label", json=body)
    lines = labeled.read_text().splitlines()
    assert len([line for line in lines if line.strip()]) == 2


def test_count_reflects_labeled(tmp_path: Path) -> None:
    corpus, labeled = _seed(tmp_path)
    client = TestClient(build_app(corpus=corpus, labeled_out=labeled))
    client.post(
        "/api/label",
        json={
            "pair": {
                "schema_version": 1,
                "genre": "g",
                "world": "w",
                "round_number": 1,
                "input_text": "hi",
                "output_text": "hello",
                "provenance": {"source_save": "x.db", "event_seq": None},
            },
            "disputes": [],
            "corrected_output": "ok",
            "labeler": "keith",
        },
    )
    resp = client.get("/api/count")
    assert resp.json() == {"unlabeled": 1, "labeled": 1}


def test_missing_corpus_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_app(corpus=tmp_path / "missing.jsonl", labeled_out=tmp_path / "out.jsonl")


def test_label_endpoint_rejects_malformed_body(tmp_path: Path) -> None:
    """pydantic validation should reject extra fields (schema is extra='forbid')."""
    corpus, labeled = _seed(tmp_path)
    client = TestClient(build_app(corpus=corpus, labeled_out=labeled))
    resp = client.post("/api/label", json={"not_a_pair": True})
    assert resp.status_code == 422


def test_pairs_endpoint_skips_malformed_line(tmp_path: Path) -> None:
    corpus, labeled = _seed(tmp_path)
    with corpus.open("a") as fh:
        fh.write("{truncated\n")
    client = TestClient(build_app(corpus=corpus, labeled_out=labeled))
    resp = client.get("/api/pairs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1  # the one valid pair, not a 500


def test_count_endpoint_skips_malformed_labeled_line(tmp_path: Path) -> None:
    corpus, labeled = _seed(tmp_path)
    labeled.write_text("{truncated\n")
    client = TestClient(build_app(corpus=corpus, labeled_out=labeled))
    resp = client.get("/api/count")
    assert resp.status_code == 200
    assert resp.json() == {"unlabeled": 1, "labeled": 0}
