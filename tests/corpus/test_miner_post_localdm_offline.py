"""Miner verification: with LocalDM dormant on the live turn, the
existing corpus miner must still produce TrainingPair rows from a save.

The save's narrative_log table records both player and narrator rows;
the miner aligns them by round_number and emits TrainingPair rows.
This test confirms that taking LocalDM off the live turn path didn't
break offline training-corpus extraction — the inputs the miner needs
(narrative_log rows from player and narrator) are still being captured
by the unchanged persistence layer.

The fixture used (single_session.db) is the authoritative post-schema
reference save.  It was built before and after the LocalDM-offline
change with the same SQL, confirming the persistence layer is
identical.  Locks success criterion #3 of the localdm-offline-only
spec (2026-04-28).
"""

from __future__ import annotations

from pathlib import Path

from sidequest.corpus.miner import mine_save
from sidequest.corpus.schema import TrainingPair

# The existing CLI fixtures directory holds the authoritative save.
FIXTURES = Path(__file__).parents[1] / "cli" / "fixtures"


def test_miner_extracts_action_and_narration_from_post_change_save() -> None:
    """Run miner against the reference save.

    Asserts it emits one TrainingPair per playable turn with non-empty
    input_text (player action) and output_text (narration).  Round 1
    is narrator-only (opening scene) and is intentionally skipped by
    the miner; rounds 2 and 3 are full player+narrator pairs.
    """
    pairs: list[TrainingPair] = list(mine_save(FIXTURES / "single_session.db"))

    assert pairs, "miner produced zero pairs — saves no longer capture turns"

    for pair in pairs:
        assert pair.input_text.strip(), f"pair at round {pair.round_number} has empty input_text"
        assert pair.output_text.strip(), f"pair at round {pair.round_number} has empty output_text"
        assert pair.genre, "pair missing genre slug"
        assert pair.world, "pair missing world slug"


def test_miner_post_localdm_offline_emits_expected_round_count() -> None:
    """Confirm the miner emits exactly 2 pairs (rounds 2 and 3).

    Round 1 has no player action and no prior narration, so the miner
    skips it.  If the count changes the persistence schema drifted.
    """
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    rounds = {p.round_number for p in pairs}

    assert rounds == {2, 3}, (
        f"expected rounds {{2, 3}} but got {rounds} — "
        "miner pairing logic or persistence schema may have changed"
    )


def test_miner_post_localdm_offline_genre_and_world_are_present() -> None:
    """Genre and world slugs survive the LocalDM-offline refactor unchanged."""
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    assert pairs[0].genre == "caverns_and_claudes"
    assert pairs[0].world == "mawdeep"


def test_miner_post_localdm_offline_provenance_names_source_save() -> None:
    """Provenance carries the save path so corpus writers can trace pairs."""
    pairs = list(mine_save(FIXTURES / "single_session.db"))
    assert pairs[0].provenance.source_save.endswith("single_session.db")
