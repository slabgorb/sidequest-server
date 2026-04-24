from __future__ import annotations

from pathlib import Path

from sidequest.corpus.diff import diff_per_player

FIXTURES = Path(__file__).parents[1] / "cli" / "fixtures"


def test_diff_pairs_same_round_across_players() -> None:
    divergences = list(diff_per_player(
        saves=[FIXTURES / "per_player_a.db", FIXTURES / "per_player_b.db"],
    ))
    round_2 = [d for d in divergences if d.round_number == 2]
    assert len(round_2) == 1
    contents = [v.content for v in round_2[0].variants]
    # Variant order follows iteration order of `saves` — don't index by position,
    # just confirm both expected fragments appear across the variants.
    combined = " | ".join(contents)
    assert "locked door" in combined
    assert "empty corridor" in combined


def test_diff_ignores_rounds_that_agree() -> None:
    divergences = list(diff_per_player(
        saves=[FIXTURES / "per_player_a.db", FIXTURES / "per_player_b.db"],
    ))
    rounds = [d.round_number for d in divergences]
    assert 1 not in rounds, f"round 1 narrator content is identical across saves, should not diverge: {rounds}"


def test_diff_single_save_emits_nothing() -> None:
    """One save = nothing to diff against."""
    divergences = list(diff_per_player(saves=[FIXTURES / "per_player_a.db"]))
    assert divergences == []


def test_diff_empty_save_list_emits_nothing() -> None:
    assert list(diff_per_player(saves=[])) == []


def test_divergence_variant_names_its_source_save() -> None:
    divergences = list(diff_per_player(
        saves=[FIXTURES / "per_player_a.db", FIXTURES / "per_player_b.db"],
    ))
    assert divergences, "expected at least one divergence"
    for v in divergences[0].variants:
        assert v.source_save.endswith(".db")
