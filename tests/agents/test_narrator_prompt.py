from sidequest.agents.narrator import NARRATOR_OUTPUT_ONLY


def test_prompt_documents_npc_side_field():
    # Closed enum surface — narrator must emit `side`.
    assert "side" in NARRATOR_OUTPUT_ONLY
    assert "player" in NARRATOR_OUTPUT_ONLY
    assert "opponent" in NARRATOR_OUTPUT_ONLY
    assert "neutral" in NARRATOR_OUTPUT_ONLY


def test_prompt_documents_beat_outcome_tiers():
    for tier in ("CritFail", "Fail", "Tie", "Success", "CritSuccess"):
        assert tier in NARRATOR_OUTPUT_ONLY


def test_prompt_documents_status_changes_field():
    assert "status_changes" in NARRATOR_OUTPUT_ONLY
    for sev in ("Scratch", "Wound", "Scar"):
        assert sev in NARRATOR_OUTPUT_ONLY
