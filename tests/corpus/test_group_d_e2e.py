# tests/corpus/test_group_d_e2e.py
"""Group D end-to-end: mine + write + read back + diff a fixture corpus.

This is the wiring test (CLAUDE.md: "Every Test Suite Needs a Wiring Test").
Individual units are covered in test_miner.py / test_writer.py / test_diff.py;
this test proves their contracts line up when chained.
"""
from __future__ import annotations

import json
from pathlib import Path

from sidequest.corpus.diff import diff_per_player
from sidequest.corpus.miner import mine_save
from sidequest.corpus.schema import TrainingPair
from sidequest.corpus.writer import write_pairs

FIXTURES = Path(__file__).parents[1] / "cli" / "fixtures"


def test_group_d_pipeline_end_to_end(tmp_path: Path) -> None:
    # Mine the single-session fixture.
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    assert pairs, "expected non-empty corpus from fixture"

    # Write JSONL.
    out = tmp_path / "corpus.jsonl"
    write_pairs(out, pairs)

    # Read back the JSONL and confirm every line round-trips through TrainingPair.
    raw_lines = out.read_text().splitlines()
    assert len(raw_lines) == len(pairs)
    for raw, original in zip(raw_lines, pairs, strict=True):
        assert json.loads(raw)["schema_version"] == 1
        parsed = TrainingPair.model_validate_json(raw)
        assert parsed == original, "JSONL round-trip changed a pair — contract drift"

    # Diff per-player fixtures and confirm round 2 diverges (Task 0 invariant).
    divergences = list(diff_per_player(
        saves=[FIXTURES / "per_player_a.db", FIXTURES / "per_player_b.db"],
    ))
    diverging_rounds = [d.round_number for d in divergences]
    assert 2 in diverging_rounds
    assert 1 not in diverging_rounds
