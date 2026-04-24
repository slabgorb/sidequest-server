"""LethalityPolicy — per-genre lethality tuning (Group C spec §4.4 + §10)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.genre.models.lethality import LethalityPolicy, VerdictsOnZeroEdge


def test_minimal_policy_roundtrips():
    policy = LethalityPolicy(
        genre_key="caverns_and_claudes",
        default_reversibility="narrative_only",
        verdicts_on_zero_edge=VerdictsOnZeroEdge(pc="humiliated", npc="defeated"),
        soul_md_constraint="genre_truth:comedic_danger_no_permadeath",
        must_narrate="A beat of slapstick pain. Keep it comedic.",
        must_not_narrate="graphic permadeath; somber elegy; last-rites speech",
    )
    assert policy.genre_key == "caverns_and_claudes"
    assert policy.default_reversibility == "narrative_only"
    assert policy.verdicts_on_zero_edge.pc == "humiliated"


def test_unknown_verdict_kind_rejected():
    """Validator must reject verdict kinds not in the LethalityVerdictKind literal."""
    with pytest.raises(ValidationError):
        VerdictsOnZeroEdge(pc="obliterated", npc="defeated")  # "obliterated" not in enum


def test_extra_fields_forbidden():
    """`extra='forbid'` catches typos in YAML — silent drop would mask content bugs."""
    with pytest.raises(ValidationError):
        LethalityPolicy(
            genre_key="x",
            default_reversibility="permanent",
            verdicts_on_zero_edge=VerdictsOnZeroEdge(pc="dead", npc="dead"),
            soul_md_constraint="x",
            must_narrate="x",
            must_not_narrate="x",
            nonsense_field=True,  # type: ignore[call-arg]
        )


def test_must_narrate_and_must_not_narrate_both_non_blank():
    """Both narrator-tone strings must be non-blank — they ship as a pair."""
    with pytest.raises(ValidationError):
        LethalityPolicy(
            genre_key="x",
            default_reversibility="permanent",
            verdicts_on_zero_edge=VerdictsOnZeroEdge(pc="dead", npc="dead"),
            soul_md_constraint="x",
            must_narrate="",
            must_not_narrate="nope",
        )
