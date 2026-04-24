from __future__ import annotations

from pathlib import Path

from sidequest.corpus.miner import mine_save

FIXTURES = Path(__file__).parents[1] / "cli" / "fixtures"


def test_mine_single_session_emits_at_least_one_pair() -> None:
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    assert len(pairs) >= 1


def test_mine_pair_carries_genre_and_world() -> None:
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    assert pairs[0].genre == "caverns_and_claudes"
    assert pairs[0].world == "mawdeep"


def test_mine_pair_has_nonempty_input_and_output() -> None:
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    for p in pairs:
        assert p.input_text, f"empty input for round {p.round_number}"
        assert p.output_text, f"empty output for round {p.round_number}"


def test_mine_provenance_names_source_save() -> None:
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    assert pairs[0].provenance.source_save.endswith("single_session.db")


def test_mine_skips_round_with_no_player_action_when_no_previous_narration() -> None:
    """Round 1 is opening narration — no prior narrator text AND no player input, so
    the miner has nothing to pair. Rounds 2+ MUST be present.
    """
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    rounds = [p.round_number for p in pairs]
    assert 2 in rounds, f"round 2 missing from {rounds}"
    assert 3 in rounds, f"round 3 missing from {rounds}"
