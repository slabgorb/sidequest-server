"""Unit tests for the 2nd-person POV swap helper (Story 49-8).

Found in the 2026-05-12 Carl/Donut/Katia caverns_sunden playtest: every
multi-card round all three connected tabs received every per-PC POV
narration card in third-person, identically. On Carl's tab, Carl's
own action card should read "You plant a boot..." instead of "Carl
plants a boot..."

The swap helper rewrites third-person references to a single named
target into second-person, using the target's pronouns to pick the
right verb conjugation and possessive forms. Pure string transform —
no network, no LLM. Lives at ``sidequest.agents.pov_swap``.

Contract (target_name, pronouns "he/him" | "she/her" | "they/them"):
    "Carl plants a boot on the moth's thorax"
      target="Carl", pronouns="he/him"
        -> "You plant a boot on the moth's thorax"

    "Donut's mace arrives a beat behind"
      target="Donut", pronouns="he/him"
        -> "Your mace arrives a beat behind"

The helper returns ``(rewritten_text, swap_count)`` so the caller can
record an OTEL span attribute ``swap_count`` for the GM panel.

These tests RED until the helper module exists. They prove:
    1. The basic name -> "You" substitution at sentence start.
    2. Possessive: "Carl's mace" -> "Your mace".
    3. Reflexive: "Carl ducks behind himself" -> "You duck behind yourself".
    4. Mid-sentence: "the bolt grazes Carl's shoulder" -> "the bolt
       grazes your shoulder".
    5. Verb conjugation: 3rd-person s drops ("plants" -> "plant",
       "watches" -> "watch", "hauls" -> "haul").
    6. Pronoun-driven conjugation for they/them: target keeps plural
       verb ("Sam ducks" with they/them: this is the singular-they
       conjugation issue — see test for the exact rule).
    7. Dialogue protection: text inside double quotes is NOT swapped.
    8. Empty target name: helper raises (fail-loud, not a silent no-op).
    9. swap_count reflects the actual number of substitutions.
"""

from __future__ import annotations

import pytest

# RED until sidequest.agents.pov_swap is created. The import must be at
# module scope so collection itself fails until Dev implements the module —
# a strong RED signal in the runner output.
from sidequest.agents.pov_swap import swap_to_second_person

# ---------------------------------------------------------------------------
# Basic substitution
# ---------------------------------------------------------------------------


def test_name_at_sentence_start_swaps_to_you_he_him():
    text = "Carl plants a boot on the moth's thorax and hauls the polearm out wet."
    out, count = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "You plant a boot on the moth's thorax and haul the polearm out wet."
    assert count >= 1, "subject swap counts as at least one substitution"


def test_name_at_sentence_start_swaps_to_you_she_her():
    text = "Katia eases the knife back out by quarter-inches."
    out, count = swap_to_second_person(text, target_name="Katia", pronouns="she/her")
    assert out == "You ease the knife back out by quarter-inches."
    assert count >= 1


def test_name_at_sentence_start_swaps_to_you_they_them():
    """Singular-they target: "Sam ducks" -> "You duck" (plural verb form
    after 'you', regardless of whether they/them is singular or plural).
    This is the standard English convention for 2nd-person 'you'."""
    text = "Sam ducks behind the pillar."
    out, count = swap_to_second_person(text, target_name="Sam", pronouns="they/them")
    assert out == "You duck behind the pillar."
    assert count >= 1


# ---------------------------------------------------------------------------
# Possessive
# ---------------------------------------------------------------------------


def test_possessive_swaps_to_your():
    text = "Donut's mace arrives a beat behind."
    out, count = swap_to_second_person(text, target_name="Donut", pronouns="he/him")
    assert out == "Your mace arrives a beat behind."
    assert count >= 1


def test_possessive_mid_sentence_swaps():
    text = "The bolt grazes Carl's shoulder before clattering off the wall."
    out, count = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "The bolt grazes your shoulder before clattering off the wall."
    assert count >= 1


# ---------------------------------------------------------------------------
# Reflexive
# ---------------------------------------------------------------------------


def test_reflexive_himself_swaps_to_yourself():
    text = "Carl shoulders himself between the moth and the door."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert "yourself" in out
    assert "himself" not in out


def test_reflexive_herself_swaps_to_yourself():
    text = "Katia braces herself against the slab."
    out, _ = swap_to_second_person(text, target_name="Katia", pronouns="she/her")
    assert "yourself" in out
    assert "herself" not in out


def test_reflexive_themself_or_themselves_swaps_to_yourself():
    """Singular-they reflexive can be 'themself' or 'themselves' — both
    surface in the playgroup's chargen since the narrator follows whatever
    flavor reads in the prose. Either form must swap."""
    text = "Sam steadies themself on the railing."
    out, _ = swap_to_second_person(text, target_name="Sam", pronouns="they/them")
    assert "yourself" in out
    assert "themself" not in out and "themselves" not in out


# ---------------------------------------------------------------------------
# Pronoun substitution — third-person -> second-person
# ---------------------------------------------------------------------------


def test_he_him_pronoun_in_predicate_swaps():
    """After the subject is rewritten to 'you', subsequent pronoun
    references to the target also need swapping: 'Carl plants a boot...
    and he hauls...' becomes 'You plant a boot... and you haul...'.
    Edge case: only swap pronouns that refer to the target — but in
    single-anchor narration there's no ambiguity, so all of them swap.
    """
    text = "Carl plants a boot and he hauls the polearm out wet."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "You plant a boot and you haul the polearm out wet."


def test_she_her_pronoun_in_predicate_swaps():
    text = "Katia eases the knife back and she watches the body for movement."
    out, _ = swap_to_second_person(text, target_name="Katia", pronouns="she/her")
    assert out == "You ease the knife back and you watch the body for movement."


def test_object_pronoun_him_swaps_to_you():
    text = "Carl plants a boot; the moth shudders against him."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert "against him" not in out
    assert "against you" in out


def test_object_pronoun_her_swaps_to_you():
    text = "Katia eases the knife; the cold seeps into her."
    out, _ = swap_to_second_person(text, target_name="Katia", pronouns="she/her")
    assert "into her" not in out
    assert "into you" in out


# ---------------------------------------------------------------------------
# Dialogue protection — text inside quotes is left alone
# ---------------------------------------------------------------------------


def test_dialogue_protected_carl_in_speech_not_swapped():
    """If another character speaks the target's name aloud, the spoken
    name is part of in-world dialogue and must NOT be rewritten — the
    speaker is referring to Carl by name, not narrating from Carl's POV.
    """
    text = 'Donut grunts, "Carl, watch the flank." Carl plants a boot.'
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    # The dialogue name stays third-person; the narrator-voice line swaps.
    assert '"Carl, watch the flank."' in out
    assert "You plant a boot." in out


def test_dialogue_protected_pronoun_in_speech_not_swapped():
    text = 'Katia hisses, "She drew first, you know." She raises the knife.'
    out, _ = swap_to_second_person(text, target_name="Katia", pronouns="she/her")
    # Dialogue's 'She drew first' refers to someone else and is in quotes;
    # the narrator-voice 'She raises' refers to Katia and swaps.
    assert '"She drew first, you know."' in out
    assert "You raise the knife." in out


# ---------------------------------------------------------------------------
# Verb conjugation — drop 3rd-person -s, handle special verb endings
# ---------------------------------------------------------------------------


def test_verb_drops_simple_s():
    text = "Carl plants the polearm."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "You plant the polearm."


def test_verb_drops_es_for_sibilant_endings():
    """Verbs ending in -ses / -shes / -ches / -xes / -zes drop -es to
    become plural form. 'watches' -> 'watch', 'hauls' -> 'haul'."""
    text = "Carl watches the door."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "You watch the door."


def test_verb_drops_ies_for_consonant_y_endings():
    """'tries' -> 'try', 'flies' -> 'fly'. The -ies suffix becomes -y."""
    text = "Carl tries the lock again."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "You try the lock again."


def test_irregular_verb_has_swaps_to_have():
    """'has' -> 'have'. This is high-frequency in narration ('Carl has the
    advantage')."""
    text = "Carl has the advantage."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "You have the advantage."


def test_irregular_verb_is_swaps_to_are():
    """'is' -> 'are'. Equally high-frequency."""
    text = "Carl is mid-swing when the moth pivots."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "You are mid-swing when the moth pivots."


def test_irregular_verb_was_swaps_to_were():
    """'was' -> 'were'. Past-tense narration uses this constantly."""
    text = "Carl was waiting for the opening."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == "You were waiting for the opening."


# ---------------------------------------------------------------------------
# Negative cases — fail-loud guards
# ---------------------------------------------------------------------------


def test_empty_target_name_raises():
    """Silent no-op on empty target_name would mask a chargen bug where
    the anchor PC has no name. Fail loud per project rule."""
    with pytest.raises(ValueError):
        swap_to_second_person("Some prose.", target_name="", pronouns="he/him")


def test_unknown_pronoun_string_raises():
    """The helper must reject pronoun strings it doesn't know how to
    swap. 'it/its', 'xe/xem', empty string, or any non-canonical form
    fail loud — silently defaulting to he/him would inject wrong
    grammar into player-facing prose."""
    with pytest.raises(ValueError):
        swap_to_second_person("Carl plants a boot.", target_name="Carl", pronouns="xe/xem")


def test_target_name_absent_returns_unchanged_and_zero_count():
    """If the target name doesn't appear in the text (atmospheric prose,
    or peer's card), the helper returns the original prose and count=0.
    No exception — atmospheric narration is a valid input."""
    text = "Rain hammers the slate. The corridor narrows."
    out, count = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert out == text
    assert count == 0


# ---------------------------------------------------------------------------
# swap_count semantics
# ---------------------------------------------------------------------------


def test_swap_count_matches_substitution_total():
    """Count should reflect the total number of distinct swap operations
    so the OTEL span has a meaningful 'how much did this rewrite' signal.
    """
    text = (
        "Carl plants a boot on the moth's thorax. "
        "Carl's polearm slides free. "
        "He shoulders himself between Donut and the door."
    )
    out, count = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    # Subject Carl x2, possessive Carl's x1, pronoun he x1, reflexive
    # himself x1 = at least 5 substitutions (verb conjugation may also
    # be counted but is implementation-detail; floor at 5).
    assert count >= 5, f"expected at least 5 swaps in the dense passage, got {count}"
    # Resulting prose has no third-person references to Carl.
    assert "Carl" not in out
    assert " he " not in out and " he," not in out and " he." not in out
    assert "himself" not in out


def test_swap_count_zero_when_no_match():
    text = "Rain falls on the slate roof."
    _, count = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert count == 0


# ---------------------------------------------------------------------------
# Multi-name safety — only the target swaps, peers do not
# ---------------------------------------------------------------------------


def test_only_target_swaps_other_pcs_untouched():
    """In MP narration that mentions multiple PCs, only the anchor swaps.
    Donut/Katia stay third-person on Carl's tab."""
    text = "Carl plants a boot; Donut's mace arrives a beat behind; Katia eases the knife back."
    out, _ = swap_to_second_person(text, target_name="Carl", pronouns="he/him")
    assert "Donut's mace" in out, "Donut must stay third-person"
    assert "Katia eases" in out, "Katia must stay third-person"
    assert "You plant a boot" in out
