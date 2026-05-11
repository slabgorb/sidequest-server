"""Story 49-2 RED — Auto-mint NPCs from prose-only dialogue mentions.

ADR-098 dropped --resume; story 49-1 restored the recent-narration block
in Recency zone. The remaining 2026-05-11 Glenross wound: the narrator
wrote dialogue about Father in detail ("He's through the back passage —
the morning room, where Mrs. Gow laid him after", "gestures you ahead of
him", "set the secateurs down on the blotter") but emitted
``npcs_present=2`` covering only Reverend Murchison + the pinafore girl.
Father never made it into the NPC roster. Turn 6 then invented "the wee
one's mother / her" with no constraint to overrule.

Story 45-53 ships a recurring-presence detector (warn on KNOWN names
that got skipped). That covers turn N when the NPC was previously
extracted but dropped from this turn's structured emission. It does NOT
cover the FIRST mention — when the narrator names a person via role or
honorific in turn N's prose for the very first time and forgets to
extract them, there is no entry to "recur" against.

This story closes that gap with a server-side prose scanner that
auto-mints ``NpcPoolMember`` entries for role-named or honorific-named
individuals that the narrator's structured patch missed. Contract:

    def _auto_mint_prose_only_npcs(
        *,
        snapshot: GameSnapshot,
        narration_text: str,
        emitted_mentions: list[NpcMention],
        turn_num: int,
    ) -> None:
        '''Scan narration prose for role-named (Father, mother, son,
        daughter, the doctor, the Reverend, the constable, ...) and
        honorific-named (Mrs. <Name>, Mr. <Name>, Dr. <Name>, Reverend
        <Name>) individuals. For each that is NOT present in
        emitted_mentions, snapshot.npcs, or snapshot.npc_pool, infer
        pronouns from surrounding prose. If pronouns are unambiguous,
        append a NpcPoolMember(drawn_from="dialogue_extraction") and
        emit npc.auto_minted_from_prose. If pronouns are ambiguous
        (no pronoun nearby, or conflicting pronouns near the same role),
        log a warning and skip mint — never guess.'''

OTEL contract (separate from story 45-53's npc.auto_registered which
fires for structured-patch mints):
- New span ``SPAN_NPC_AUTO_MINTED_FROM_PROSE = "npc.auto_minted_from_prose"``
  in ``sidequest/telemetry/spans/npc.py`` with a ``SpanRoute`` so the
  watcher emits a ``state_transition`` event with
  ``op="auto_minted_from_prose"`` under ``component="npc_registry"``.
  Attributes: ``npc_name``, ``role``, ``pronouns``, ``source``,
  ``turn_number``.
- Helper context manager ``npc_auto_minted_from_prose_span(...)``
  exported from ``sidequest.telemetry.spans``.

Wiring contract:
- ``_apply_narration_result_to_snapshot`` must invoke the auto-minter
  AFTER ``_detect_missed_recurring_npcs`` so the recurring-presence
  detector still gets first crack at KNOWN names. The auto-minter
  then mints only FIRST-mention names.

OTEL Observability Principle (CLAUDE.md): the ambiguous-pronoun skip
path also fires an OTEL span (distinct event name) so Sebastien's GM
panel can see when the system bites its tongue. Without a span the
skip is invisible.

Related: ADR-031 Game Watcher / OTEL, CLAUDE.md OTEL Observability
Principle, SOUL.md "Living World" (NPCs that recur deserve names),
session_helpers.py:660 detector (sibling pattern).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult, NpcMention
from sidequest.game.creature_core import CreatureCore
from sidequest.game.npc_pool import NpcPoolMember
from sidequest.game.session import GameSnapshot, Npc
from tests._helpers.session_room import room_for

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

SPAN_NAME = "npc.auto_minted_from_prose"


def _core(name: str) -> CreatureCore:
    return CreatureCore(name=name, description="X.", personality="Y.")


def _result(
    narration: str, npcs_present: list[NpcMention] | None = None
) -> NarrationTurnResult:
    return NarrationTurnResult(
        narration=narration,
        npcs_present=list(npcs_present or []),
        is_degraded=False,
    )


def _minted_spans(
    otel_capture: "InMemorySpanExporter",
    expected_role: str | None = None,
    expected_name: str | None = None,
) -> list:
    """Filter captured spans by the auto-mint span name and optional role/name."""
    spans = [s for s in otel_capture.get_finished_spans() if s.name == SPAN_NAME]
    if expected_role is not None:
        spans = [
            s
            for s in spans
            if (s.attributes or {}).get("role", "").casefold() == expected_role.casefold()
        ]
    if expected_name is not None:
        spans = [
            s
            for s in spans
            if (s.attributes or {}).get("npc_name", "").casefold() == expected_name.casefold()
        ]
    return spans


def _pool_member(snapshot: GameSnapshot, *, role: str) -> NpcPoolMember | None:
    """Find a pool member by case-folded role tag."""
    target = role.casefold()
    for member in snapshot.npc_pool:
        if (member.role or "").casefold() == target:
            return member
    return None


# ---------------------------------------------------------------------------
# Span-catalog tests (AC3 — OTEL contract)
# ---------------------------------------------------------------------------


def test_span_npc_auto_minted_from_prose_is_defined_in_catalog():
    """Per the OTEL Observability Principle, the auto-minter must register
    a stable span name in the telemetry catalog so the GM panel and
    routing tests see it. The constant must be exactly
    ``npc.auto_minted_from_prose`` — distinct from
    ``npc.auto_registered`` (which fires when the narrator's structured
    patch includes the NPC) so Sebastien can tell which path minted any
    given NPC.
    """
    from sidequest.telemetry import spans as spans_module

    assert hasattr(spans_module, "SPAN_NPC_AUTO_MINTED_FROM_PROSE"), (
        "SPAN_NPC_AUTO_MINTED_FROM_PROSE missing from telemetry catalog "
        "— without it the GM panel cannot distinguish prose-only mints "
        "from structured-patch mints, and the lie-detector loses its "
        "first-mention signal."
    )
    assert spans_module.SPAN_NPC_AUTO_MINTED_FROM_PROSE == SPAN_NAME, (
        f"Span name must be exactly {SPAN_NAME!r} for the GM panel filter to match."
    )


def test_npc_auto_minted_from_prose_span_is_routed():
    """Every live span must be either routed (in SPAN_ROUTES) or flat-only
    (in FLAT_ONLY_SPANS). Auto-minted-from-prose is a state_transition
    event under the npc_registry component (parallel to
    npc.auto_registered).
    """
    from sidequest.telemetry.spans import SPAN_ROUTES

    assert SPAN_NAME in SPAN_ROUTES, (
        f"{SPAN_NAME!r} not in SPAN_ROUTES — GameWatcher will drop it on "
        "the floor and the GM panel will never receive the event."
    )
    route = SPAN_ROUTES[SPAN_NAME]
    assert route.event_type == "state_transition", (
        "Auto-mint events must route as state_transition so the GM panel "
        "renders them under the same lane as npc.auto_registered."
    )
    assert route.component == "npc_registry", (
        "Component must be 'npc_registry' to share the GM-panel column "
        "with other NPC-state spans (auto_registered, referenced, "
        "reinvented, recurring_presence_missed)."
    )


def test_npc_auto_minted_from_prose_span_helper_is_exported():
    """The context-manager helper must be exported from
    ``sidequest.telemetry.spans`` so production code can ``from
    sidequest.telemetry.spans import npc_auto_minted_from_prose_span``
    without reaching into the submodule (parallel to
    ``npc_recurring_presence_missed_span``).
    """
    from sidequest.telemetry import spans as spans_module

    assert hasattr(spans_module, "npc_auto_minted_from_prose_span"), (
        "npc_auto_minted_from_prose_span helper must be exported from "
        "sidequest.telemetry.spans (parallel to npc_auto_registered_span)."
    )


def test_auto_minted_from_prose_span_attributes_round_trip_via_route():
    """The SpanRoute extract function must surface the five attributes the
    GM panel needs: name, role, pronouns, source, turn_number. We feed
    a minimal stub through the extract lambda and verify the dict shape.
    """
    from sidequest.telemetry.spans import SPAN_ROUTES

    pytest.importorskip("opentelemetry")
    route = SPAN_ROUTES[SPAN_NAME]

    class _Stub:
        attributes = {
            "npc_name": "Father",
            "role": "father",
            "pronouns": "he/him",
            "source": "dialogue_extraction",
            "turn_number": 5,
        }

    extracted = route.extract(_Stub())  # type: ignore[arg-type]
    assert extracted.get("op") == "auto_minted_from_prose", (
        "op must be 'auto_minted_from_prose' so the GM panel can filter "
        "on this specific subroute (distinct from 'auto_registered')."
    )
    assert extracted.get("name") == "Father"
    assert extracted.get("role") == "father"
    assert extracted.get("pronouns") == "he/him"
    assert extracted.get("source") == "dialogue_extraction"
    assert extracted.get("turn_number") == 5


# ---------------------------------------------------------------------------
# Unit tests — role-only prose (AC1)
# ---------------------------------------------------------------------------


def test_role_father_with_male_pronoun_mints_pool_member(otel_capture):
    """The headline AC5 fixture: 'the patient is Father; he is grave'.
    Auto-minter must add an NpcPoolMember with role='father',
    pronouns='he/him', drawn_from='dialogue_extraction'.
    """
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="The patient is Father; he is grave.",
        emitted_mentions=[],
        turn_num=5,
    )
    member = _pool_member(snapshot, role="father")
    assert member is not None, (
        "Auto-mint must append a NpcPoolMember with role='father' when "
        "the prose names a Father and surrounds him with male pronouns. "
        f"Current pool: {[(m.name, m.role, m.pronouns) for m in snapshot.npc_pool]}"
    )
    assert member.pronouns == "he/him", (
        f"Pronouns must be 'he/him' (inferred from surrounding 'he'), got {member.pronouns!r}."
    )
    assert member.drawn_from == "dialogue_extraction", (
        "drawn_from must be 'dialogue_extraction' so persistence/replay "
        "distinguishes prose-mint provenance from structured "
        "narrator_invented mints."
    )


def test_role_father_mints_emits_otel_span(otel_capture):
    """Wiring + telemetry: minting a Father from prose must emit the
    ``npc.auto_minted_from_prose`` span with all five attributes set."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="Father lies pale. He cannot answer.",
        emitted_mentions=[],
        turn_num=5,
    )
    spans = _minted_spans(otel_capture, expected_role="father")
    assert len(spans) == 1, (
        "Exactly one span must fire when one role/individual is minted. "
        f"Saw {len(spans)} matching spans (all auto-mint spans: "
        f"{[(s.attributes or {}).get('npc_name') for s in otel_capture.get_finished_spans() if s.name == SPAN_NAME]})."
    )
    attrs = spans[0].attributes or {}
    assert attrs.get("role") == "father"
    assert attrs.get("pronouns") == "he/him"
    assert attrs.get("source") == "dialogue_extraction", (
        "source attribute must be 'dialogue_extraction' to mark prose-"
        "extraction provenance distinct from narrator_invented."
    )
    assert attrs.get("turn_number") == 5
    assert attrs.get("npc_name"), (
        "npc_name attribute must not be empty — the GM panel renders this "
        "as the row title."
    )


def test_role_mother_with_female_pronoun_mints_pool_member(otel_capture):
    """Mirror of the Father case. AC1 enumerates mother explicitly."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text=(
            "The wee one's mother kneels at the hearth. She does not look up."
        ),
        emitted_mentions=[],
        turn_num=6,
    )
    member = _pool_member(snapshot, role="mother")
    assert member is not None, (
        "Mother in prose with nearby 'She' must produce a NpcPoolMember "
        "with role='mother' pronouns='she/her'."
    )
    assert member.pronouns == "she/her"


def test_role_the_doctor_with_male_pronoun_mints(otel_capture):
    """AC1 enumerates 'the doctor' as a role token. The article+role form
    ('the doctor') is the most common honorific style in narrative prose;
    the scanner must catch it the same way it catches bare-role 'Father'.
    """
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="The doctor sets his bag down. He glances at the body.",
        emitted_mentions=[],
        turn_num=3,
    )
    member = _pool_member(snapshot, role="doctor")
    assert member is not None, (
        "'the doctor' is the canonical example role from AC1 — must mint."
    )
    assert member.pronouns == "he/him"


def test_role_reverend_bare_with_male_pronoun_mints(otel_capture):
    """AC1: 'the Reverend' with surrounding pronoun. (When the prose
    follows with a proper name like 'Reverend Murchison' that's the
    honorific+name path, tested separately.)"""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="The Reverend lifts his hands. He intones the rite.",
        emitted_mentions=[],
        turn_num=2,
    )
    member = _pool_member(snapshot, role="reverend")
    assert member is not None
    assert member.pronouns == "he/him"


# ---------------------------------------------------------------------------
# Unit tests — honorific + name prose (AC1 second half)
# ---------------------------------------------------------------------------


def test_honorific_mrs_with_name_mints_with_female_pronouns(otel_capture):
    """AC1: 'Mrs. <Name>' is one of the listed honorific forms. The mint
    must use the full 'Mrs. Gow' as the npc_name and infer 'she/her' from
    surrounding prose. This is the 2026-05-11 Glenross 'Mrs. Gow laid
    him after' shape (where the narrator named Mrs. Gow in prose but
    omitted her from npcs_present)."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text=(
            "Mrs. Gow tended the body. She washed him and laid him out."
        ),
        emitted_mentions=[],
        turn_num=5,
    )
    # The mint should appear by name (Mrs. Gow) regardless of internal
    # role-tagging conventions. We pin observable identity: she/her and
    # name contains 'Gow' so the GM panel can find her.
    matched = [m for m in snapshot.npc_pool if "gow" in m.name.casefold()]
    assert len(matched) == 1, (
        "Mrs. Gow named in prose with nearby female pronouns must mint "
        "exactly one pool member. "
        f"Pool: {[(m.name, m.pronouns) for m in snapshot.npc_pool]}"
    )
    member = matched[0]
    assert member.pronouns == "she/her"
    assert member.drawn_from == "dialogue_extraction"


def test_honorific_mr_with_name_mints_with_male_pronouns(otel_capture):
    """Parallel to Mrs. <Name>: Mr. <Name> + 'he' nearby."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="Mr. Hodge nods curtly. He says nothing more.",
        emitted_mentions=[],
        turn_num=1,
    )
    matched = [m for m in snapshot.npc_pool if "hodge" in m.name.casefold()]
    assert len(matched) == 1
    assert matched[0].pronouns == "he/him"


def test_honorific_doctor_with_name_mints(otel_capture):
    """Dr. <Name> is enumerated in AC1. Same shape as Mrs./Mr."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="Dr. Sallow opens her satchel. She examines the wound.",
        emitted_mentions=[],
        turn_num=4,
    )
    matched = [m for m in snapshot.npc_pool if "sallow" in m.name.casefold()]
    assert len(matched) == 1
    assert matched[0].pronouns == "she/her"


# ---------------------------------------------------------------------------
# Unit tests — pronoun inference rules (AC2)
# ---------------------------------------------------------------------------


def test_ambiguous_pronouns_no_pronoun_skips_mint_and_warns(
    otel_capture, caplog
):
    """AC2 — fail loud, do NOT guess. 'The doctor said something.' with no
    he/she/they anywhere in the prose: skip mint AND log a warning naming
    the role we couldn't resolve. The auto_minted span must NOT fire.
    """
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    with caplog.at_level(logging.WARNING):
        _auto_mint_prose_only_npcs(
            snapshot=snapshot,
            narration_text="The doctor said something. The wind picked up.",
            emitted_mentions=[],
            turn_num=2,
        )

    assert _pool_member(snapshot, role="doctor") is None, (
        "AC2 violation: 'the doctor' with no surrounding pronouns must "
        "NOT be auto-minted. Guessing pronouns would create the same "
        "kind of canonized hallucination this story is supposed to "
        "prevent (a doctor with arbitrary pronouns then becomes a "
        "constraint on future turns)."
    )
    assert _minted_spans(otel_capture) == [], (
        "No auto_minted_from_prose span may fire when the mint is "
        "skipped — the GM panel must see the skip via a distinct signal, "
        "not by mistaking the absence of a span for success."
    )
    matched_warn = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and "doctor" in r.getMessage().casefold()
    ]
    assert matched_warn, (
        "AC2 'warn (log) + skip mint': a WARNING-level log must name the "
        "role/individual the auto-minter skipped due to ambiguous "
        "pronouns. Without the log developers reading the file have no "
        f"signal that the lie-detector tongue-bit. Caplog records: {[r.getMessage() for r in caplog.records]}"
    )


def test_ambiguous_pronouns_conflicting_skips_mint_and_warns(
    otel_capture, caplog
):
    """AC2 — conflicting pronouns near the role are ambiguous too. 'The
    doctor said... she walked over... he opened the door.' Mixed he and
    she near 'doctor' must NOT resolve to either; skip mint."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    with caplog.at_level(logging.WARNING):
        _auto_mint_prose_only_npcs(
            snapshot=snapshot,
            narration_text=(
                "The doctor enters. She moves to the window. He opens the door."
            ),
            emitted_mentions=[],
            turn_num=4,
        )
    assert _pool_member(snapshot, role="doctor") is None, (
        "Two conflicting pronouns near a single role-mention is ambiguous "
        "by AC2 — must skip rather than pick one."
    )
    assert _minted_spans(otel_capture) == []


def test_pronoun_window_is_local_not_full_text(otel_capture):
    """Pronouns must be near the role mention, not anywhere in the
    paragraph. A pronoun at the start of a long passage that resolves
    to a different entity cannot apply to a role mentioned 200+ chars
    later. Without this guard the scanner would happily attach an
    earlier 'she' (referring to Mrs. Hardin) to a much later 'the
    constable' — fabricating gender from coincident proximity.

    Contract: the constable mention with no LOCAL pronoun must be
    treated as AC2 ambiguous → skip mint. The earlier 'She' is too far
    away to count. The test asserts the SKIP outcome explicitly (not
    just "didn't pick the wrong pronouns") so the failure mode of a
    too-greedy pronoun grab is caught.
    """
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    # 'she' belongs to the opening clause (Mrs. Hardin); 'the constable'
    # appears 300+ characters later with no pronoun nearby.
    narration = (
        "Mrs. Hardin presses the cool cloth to her brow. She breathes "
        "slowly. The hour stretches. Outside, the rain lifts and falls, "
        "and the lamp-oil burns lower. Voices from the corridor —"
        " someone calling for tea, someone else laughing, the dog "
        "scratching at the back door. A long while later — long after "
        "the bell has rung the half — the constable arrives at the gate."
    )
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text=narration,
        emitted_mentions=[],
        turn_num=7,
    )
    assert _pool_member(snapshot, role="constable") is None, (
        "AC2 + locality: the constable mention has no pronoun within "
        "its local window. The opening 'She' refers to Mrs. Hardin "
        "(300+ chars earlier). The auto-minter must NOT reach across "
        "intervening clauses to claim a pronoun — that produces "
        "fabricated-from-proximity gender. Skip the mint instead. "
        f"Pool after apply: {[(m.name, m.role, m.pronouns) for m in snapshot.npc_pool]}"
    )
    assert _minted_spans(otel_capture, expected_role="constable") == [], (
        "No constable auto_minted_from_prose span may fire — the "
        "scanner correctly bit its tongue."
    )


def test_they_them_pronouns_are_inferred(otel_capture):
    """AC2 enumerates they/them as valid. A nonbinary or unknown-gender
    NPC must mint with they/them if that's what the narrator used."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text=(
            "The constable arrives at the gate. They unbutton their coat "
            "and stamp the mud from their boots."
        ),
        emitted_mentions=[],
        turn_num=3,
    )
    member = _pool_member(snapshot, role="constable")
    assert member is not None
    assert member.pronouns == "they/them", (
        f"Singular 'they/their' near the role must produce pronouns "
        f"'they/them' (got {member.pronouns!r})."
    )


# ---------------------------------------------------------------------------
# Unit tests — dedup against existing stores and emitted mentions
# ---------------------------------------------------------------------------


def test_skip_when_role_already_in_emitted_mentions(otel_capture):
    """If the narrator DID emit the role in npcs_present, the auto-minter
    must NOT double-mint. This is the contract that makes the auto-mint
    purely additive — it only kicks in when the narrator forgot."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="Father turns to you. He nods slowly.",
        emitted_mentions=[NpcMention(name="Father", role="father")],
        turn_num=5,
    )
    assert _pool_member(snapshot, role="father") is None, (
        "Auto-mint must NOT fire when the narrator already emitted the "
        "role in npcs_present — that's a double-extract."
    )
    assert _minted_spans(otel_capture) == []


def test_skip_when_name_already_in_existing_npcs(otel_capture):
    """If the name (Mrs. Gow) is already a stateful Npc, no auto-mint —
    this is recurring-presence territory handled by story 45-53."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot(npcs=[Npc(core=_core("Mrs. Gow"))])
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="Mrs. Gow folded the linen. She closed the door.",
        emitted_mentions=[],
        turn_num=8,
    )
    assert _minted_spans(otel_capture) == [], (
        "Mrs. Gow already in snapshot.npcs — auto-minter must defer to "
        "the recurring-presence detector (45-53). Double-minting would "
        "create a pool-member shadow that drift-detection then fights."
    )


def test_skip_when_name_already_in_npc_pool(otel_capture):
    """If the name is in npc_pool already (from a prior turn's mint, an
    archetype draw, or world-authoring), no double-mint."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot(
        npc_pool=[
            NpcPoolMember(name="Mrs. Gow", pronouns="she/her", drawn_from="dialogue_extraction")
        ]
    )
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="Mrs. Gow refolded the linen. She did not look up.",
        emitted_mentions=[],
        turn_num=8,
    )
    # Pool started with 1; must still be 1.
    assert len(snapshot.npc_pool) == 1, (
        "Mrs. Gow already in npc_pool — auto-minter must NOT append a "
        f"duplicate. Pool: {[(m.name, m.drawn_from) for m in snapshot.npc_pool]}"
    )


def test_skip_when_role_token_is_pc_name(otel_capture):
    """A PC named (e.g.) 'Father' as their character name must never be
    promoted into the pool via the role scanner — the PC filter that
    protects _apply_npc_mentions applies here too. Parallel guard to
    story 45-53's test_detector_skips_pc_names.
    """
    from sidequest.game.character import Character
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    pc = Character(
        core=_core("Father"),
        backstory="A pious wanderer.",
        char_class="adventurer",
        race="human",
    )
    snapshot = GameSnapshot(characters=[pc])
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text="Father raises his hand. He shakes his head.",
        emitted_mentions=[],
        turn_num=3,
    )
    assert _pool_member(snapshot, role="father") is None, (
        "PC named 'Father' must not be promoted into npc_pool as role "
        "'father' — the PC filter is the only thing keeping this clean."
    )
    assert _minted_spans(otel_capture) == []


# ---------------------------------------------------------------------------
# Unit tests — word-boundary precision
# ---------------------------------------------------------------------------


def test_role_word_boundary_not_substring(otel_capture):
    """'Father' must word-boundary match. 'fatherland', 'feather', and
    'forefather' inside prose must NOT trigger a mint — substring matching
    would false-positive the pool with phantom NPCs every turn the
    narrator uses common English compound words. This mirrors the
    word-boundary contract in _detect_missed_recurring_npcs."""
    from sidequest.server.session_helpers import _auto_mint_prose_only_npcs

    snapshot = GameSnapshot()
    _auto_mint_prose_only_npcs(
        snapshot=snapshot,
        narration_text=(
            "The fatherland is far behind us. He marches steadily. "
            "A forefather's portrait hangs crooked on the wall."
        ),
        emitted_mentions=[],
        turn_num=2,
    )
    assert _pool_member(snapshot, role="father") is None, (
        "Substring 'father' inside 'fatherland' / 'forefather' must NOT "
        "produce a role-mint. Word-boundary regex (\\bfather\\b on "
        "case-folded text) is the only safe match policy."
    )


# ---------------------------------------------------------------------------
# Wiring test — auto-minter is reachable from the production apply path.
# CLAUDE.md "Every Test Suite Needs a Wiring Test" mandate.
# ---------------------------------------------------------------------------


def test_wiring_apply_narration_result_invokes_auto_minter(otel_capture):
    """Integration: the auto-minter must be wired into
    ``_apply_narration_result_to_snapshot`` so production turns exercise
    it without an explicit caller. A narration whose prose names Father
    with male pronouns and an empty npcs_present must produce both
    (a) a NpcPoolMember(role='father', pronouns='he/him') in the
    snapshot and (b) the auto_minted_from_prose span, end-to-end.

    This is the wire-first guard. Unit tests on the auto-minter alone
    do NOT prove the production code path engages it — only this test
    does. (CLAUDE.md "Verify Wiring, Not Just Existence".)
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snapshot = GameSnapshot(genre_slug="test", world_slug="test")
    snapshot.character_locations["Hero"] = "Manse"

    result = _result(
        narration=(
            "Father lies pale against the linen. He cannot speak."
        ),
        npcs_present=[],  # the bug: narrator forgot to emit Father
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        "player",
        room=room_for(snapshot),
        acting_character_name="Hero",
    )

    member = _pool_member(snapshot, role="father")
    assert member is not None, (
        "Wiring failure: _apply_narration_result_to_snapshot must invoke "
        "_auto_mint_prose_only_npcs so production turns surface Father "
        "into the pool. Unit tests on the auto-minter passed but the "
        "production path does not call it — exactly the failure mode "
        "CLAUDE.md's 'Verify Wiring, Not Just Existence' principle "
        "warns about."
    )
    assert member.pronouns == "he/him"
    assert member.drawn_from == "dialogue_extraction"

    spans = _minted_spans(otel_capture, expected_role="father")
    assert len(spans) == 1, (
        "Wiring + telemetry: exactly one auto_minted_from_prose span "
        "must fire when the apply path mints Father."
    )


def test_wiring_apply_narration_result_silent_when_narrator_emitted(otel_capture):
    """The wiring path must NOT false-positive when the narrator does the
    right thing — Father in prose AND in npcs_present: zero auto-mint
    spans end-to-end. (Mirrors story 45-53's silent-when-emitted test.)
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snapshot = GameSnapshot(genre_slug="test", world_slug="test")
    snapshot.character_locations["Hero"] = "Manse"

    result = _result(
        narration="Father lies pale. He cannot speak.",
        npcs_present=[NpcMention(name="Father", role="father", pronouns="he/him")],
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        "player",
        room=room_for(snapshot),
        acting_character_name="Hero",
    )

    assert _minted_spans(otel_capture) == [], (
        "End-to-end happy path: when the narrator emits Father in "
        "npcs_present, no auto_minted_from_prose span fires (the "
        "structured path's npc.auto_registered handles it instead)."
    )


def test_wiring_auto_minter_runs_after_recurring_presence_detector(otel_capture):
    """Story 45-53's recurring-presence detector handles KNOWN names that
    got skipped from emission. Story 49-2's auto-minter handles UNKNOWN
    (first-mention) names. Both must run in the apply pipeline so a
    narration that does both (skip a known NPC AND first-mention a new
    one) produces BOTH signals. This pins the wiring order and the fact
    that the two detectors do not stomp each other.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snapshot = GameSnapshot(
        genre_slug="test",
        world_slug="test",
        npcs=[Npc(core=_core("Boris"), last_seen_turn=4)],  # known recurring
    )
    snapshot.character_locations["Hero"] = "Tavern"

    result = _result(
        narration=(
            "Boris pours another round. The doctor watches from the corner. "
            "He says nothing."
        ),
        npcs_present=[],  # narrator forgot BOTH
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        "player",
        room=room_for(snapshot),
        acting_character_name="Hero",
    )

    # Auto-mint fired for the doctor (first-mention).
    assert _pool_member(snapshot, role="doctor") is not None, (
        "Auto-minter must mint 'the doctor' as a first-mention role even "
        "in the presence of a parallel recurring-presence miss."
    )
    # Recurring-presence detector fired for Boris (sibling pattern).
    recurring_spans = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == "npc.recurring_presence_missed"
    ]
    assert recurring_spans, (
        "Recurring-presence detector (45-53) must still fire for Boris — "
        "the auto-minter does not subsume or replace it. The two signals "
        "are distinct lie-detector channels."
    )
