"""Story 49-2 RED — Glenross save replay against the auto-minter.

Replay the 2026-05-11 Glenross playtest save to confirm the prose-only
NPC auto-mint resolves the remaining wound left by story 49-1:

1. **Father never reaches the roster.** Turn 5 narrator prose detailed
   Father ("He's through the back passage", "Mrs. Gow laid him after",
   "set the secateurs down on the blotter") but emitted
   ``npcs_present=2`` covering only Reverend Murchison and the pinafore
   girl. Father lived only in prose. The pool had no Father.

2. **Turn 6 invented "mother".** With no Father in the pool, the
   narrator drafted turn 6 around "the wee one's mother / her" because
   nothing in the roster contradicted that invention.

The fix this VERIFY pins:

- ``_apply_narration_result_to_snapshot`` invokes
  ``_auto_mint_prose_only_npcs`` after the recurring-presence detector.
- Replaying turn 5's narration (against an empty pool) mints a Father
  pool member with ``role='father'`` and ``pronouns='he/him'``.
- Replaying turn 6's narration (against a snapshot that already
  contains Father in the pool) does NOT mint a separate "mother"
  pool member — pronouns are ambiguous in the turn-6 fragment, OR the
  existing Father resolves the reference. Either resolution is
  acceptable; what is NOT acceptable is silently canonizing a brand-new
  "mother" NPC with female pronouns alongside Father.

Skipped gracefully when the save db is not on disk so CI stays
hermetic; the test is load-bearing on Keith's machine and on any
playtester rerunning Story 49-2 (parallel skip pattern to
``test_glenross_replay_recency_window.py``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult, NpcMention
from sidequest.game.npc_pool import NpcPoolMember
from sidequest.game.session import GameSnapshot
from tests._helpers.session_room import room_for

GLENROSS_SAVE = (
    Path.home() / ".sidequest" / "saves" / "games" / "2026-05-11-glenross" / "save.db"
)
CONTENT_GENRE_PACKS = (
    Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
)

# Distinct fragments from the 2026-05-11 Glenross narrator prose. Used to
# build the turn-5 and turn-6 narration result fixtures the replay
# applies. These are paraphrased from the save's narrative_log; tests
# are robust to minor wording drift since they assert on the *minted
# pool member shape*, not on exact prose-string echo.
TURN_5_NARRATION = (
    "The Reverend leads you down the corridor. He's through the back "
    "passage — the morning room, where Mrs. Gow laid him after. Father "
    "lies pale against the linen. He cannot speak. The Reverend sets "
    "the secateurs down on the blotter beside him."
)
TURN_6_NARRATION_BUGGY = (
    "You bend to the wee one's mother. She is through here, behind "
    "the screen, her breath shallow."
)


# ---------------------------------------------------------------------------
# Save-loader helpers (mirrors test_glenross_replay_recency_window.py)
# ---------------------------------------------------------------------------


def _load_glenross_snapshot() -> GameSnapshot:
    conn = sqlite3.connect(str(GLENROSS_SAVE))
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM game_state WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "glenross save has no game_state row"
    return GameSnapshot.model_validate(json.loads(row[0]))


# ---------------------------------------------------------------------------
# Skip-on-missing guard — CI stays hermetic; local replay is the use case.
# Save AND content pack must both exist or the test is meaningless.
# ---------------------------------------------------------------------------


pytestmark = [
    pytest.mark.skipif(
        not GLENROSS_SAVE.exists(),
        reason=(
            f"glenross save not present at {GLENROSS_SAVE}; this is the live "
            "replay test from Story 49-2 and runs on Keith's machine"
        ),
    ),
    pytest.mark.skipif(
        not (CONTENT_GENRE_PACKS / "tea_and_murder").exists(),
        reason="sidequest-content/genre_packs/tea_and_murder not checked out",
    ),
]


# ---------------------------------------------------------------------------
# AC #6 part 1: turn 5 replay mints Father
# ---------------------------------------------------------------------------


def test_glenross_turn5_replay_auto_mints_father():
    """Replay turn 5 narration against the Glenross save's pre-turn-5
    snapshot state. The prose names Father with male pronouns; the
    narrator's emitted npcs_present omits him (the bug). The
    auto-minter MUST append a Father pool member with role='father'
    pronouns='he/him' drawn_from='dialogue_extraction'.

    Acting characters: Ziggy is the host PC in this save. Pre-turn-5,
    Father is not in the pool — verify that precondition before the
    apply call.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snapshot = _load_glenross_snapshot()

    # Precondition: no Father (by role or by name) currently in the pool.
    # If a prior playtest had already minted him, the test's apply call
    # would be vacuous. Clear if needed — this is a hermetic replay.
    snapshot.npc_pool = [
        m
        for m in snapshot.npc_pool
        if (m.role or "").casefold() != "father"
        and m.name.casefold() != "father"
    ]
    # Belt + braces: also strip any stateful Npc named "Father".
    snapshot.npcs = [n for n in snapshot.npcs if n.core.name.casefold() != "father"]

    # The 2026-05-11 narrator emitted only Reverend Murchison + the
    # pinafore girl in npcs_present. Father was missing — that's the
    # bug we're replaying.
    result = NarrationTurnResult(
        narration=TURN_5_NARRATION,
        npcs_present=[
            NpcMention(name="Reverend Murchison", role="reverend", pronouns="he/him"),
            NpcMention(name="the pinafore girl", role="child", pronouns="she/her"),
        ],
        is_degraded=False,
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        "player",
        room=room_for(snapshot, slug=snapshot.world_slug),
        acting_character_name="Ziggy",
    )

    fathers = [
        m
        for m in snapshot.npc_pool
        if (m.role or "").casefold() == "father" or m.name.casefold() == "father"
    ]
    assert len(fathers) == 1, (
        "After turn-5 replay, exactly one Father must exist in the "
        "pool — auto-minter caught the prose-only mention the "
        "narrator skipped. "
        f"Found {len(fathers)}: {[(m.name, m.role, m.pronouns, m.drawn_from) for m in fathers]}"
    )
    father = fathers[0]
    assert father.pronouns == "he/him", (
        f"Father's pronouns must be 'he/him' from surrounding 'He' / "
        f"'him' in the turn-5 prose (got {father.pronouns!r})."
    )
    assert father.drawn_from == "dialogue_extraction", (
        "drawn_from must mark this as a prose-extraction mint, distinct "
        "from narrator_invented. Forensic queries depend on it: "
        "'which NPCs did the auto-minter catch this session?'"
    )


# ---------------------------------------------------------------------------
# AC #6 part 2: turn 6 replay with populated roster does not double-mint
# ---------------------------------------------------------------------------


def test_glenross_turn6_replay_with_father_in_pool_does_not_mint_mother():
    """Replay turn 6's buggy narration against a snapshot that ALREADY
    contains Father in the pool (post-turn-5 auto-mint). The turn-6
    narration referenced 'the wee one's mother / she' — the bug-frame
    invention.

    The contract this test pins: the auto-minter must NOT silently
    canonize a brand-new 'mother' NpcPoolMember alongside Father.
    Acceptable outcomes:
      (a) No mother minted — pronouns are too tangled for clean
          inference, AC2 fail-loud skip wins, OR
      (b) The pool grew by zero (the existing Father shadows / the
          turn-6 prose is too short for clean role match).

    NOT acceptable: pool grows by +1 with a 'mother' member with
    'she/her' pronouns. That's the regression — the auto-minter
    becoming the source of the same hallucination the recency window
    was supposed to prevent.

    (Note: behavioral correctness — i.e. the narrator producing the
    *right* prose this turn — lives in story 49-1's prompt fix.
    49-2's job is to ensure the auto-minter doesn't make 49-1's win
    worse by canonizing the bug.)
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snapshot = _load_glenross_snapshot()
    # Set up the post-turn-5 state: Father is in the pool with male
    # pronouns. (Reconstructed manually since the live save predates
    # the auto-mint feature.)
    snapshot.npc_pool = [
        m
        for m in snapshot.npc_pool
        if (m.role or "").casefold() != "father"
        and m.name.casefold() != "father"
    ]
    snapshot.npc_pool.append(
        NpcPoolMember(
            name="Father",
            role="father",
            pronouns="he/him",
            drawn_from="dialogue_extraction",
        )
    )
    pool_size_before = len(snapshot.npc_pool)

    result = NarrationTurnResult(
        narration=TURN_6_NARRATION_BUGGY,
        npcs_present=[],  # the bug — narrator extracted nothing
        is_degraded=False,
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        "player",
        room=room_for(snapshot, slug=snapshot.world_slug),
        acting_character_name="Ziggy",
    )

    # Look specifically for a fresh 'mother' role-mint.
    mothers = [m for m in snapshot.npc_pool if (m.role or "").casefold() == "mother"]
    assert not mothers, (
        "AC6 regression: with Father already in the pool, the turn-6 "
        "buggy narration must NOT silently canonize a 'mother' "
        "NpcPoolMember alongside him. That would turn the auto-minter "
        "into the source of the same gender-flip hallucination story "
        "49-1 was built to prevent. "
        f"Found mothers: {[(m.name, m.pronouns, m.drawn_from) for m in mothers]}"
    )
    # Pool may have grown for non-mother reasons (e.g. some other role
    # in the prose); but the Father entry must still be present and
    # unchanged.
    fathers_after = [m for m in snapshot.npc_pool if (m.role or "").casefold() == "father"]
    assert len(fathers_after) == 1, (
        "Father must remain in the pool after turn-6 replay — exactly "
        "one entry, pronouns preserved. The turn-6 path must not "
        "mutate or remove the existing Father."
    )
    assert fathers_after[0].pronouns == "he/him", (
        "Father's pronouns must NOT have been overwritten to 'she/her' "
        "by the turn-6 'She is through here' fragment — pool-member "
        "identity fields are write-once per ``_apply_npc_mentions`` "
        "additive-upsert rules."
    )
    # The turn-6 fixture has exactly one role-mention ("mother") which the
    # gender-paired guard must block. No other role tokens or honorifics
    # appear in TURN_6_NARRATION_BUGGY ("You bend to the wee one's mother.
    # She is through here, behind the screen, her breath shallow."). Pool
    # size must therefore be unchanged. Tightened from <= +2 slack to ==
    # exact during Reviewer rework — the slack mode would let a greedy
    # auto-minter with 1–2 false positives slip past undetected.
    assert len(snapshot.npc_pool) == pool_size_before, (
        f"Pool size must be unchanged after turn-6 replay "
        f"({pool_size_before} → {len(snapshot.npc_pool)}): the only "
        "role-mention in the buggy narration is 'mother', which the "
        "gender-paired guard blocks against the pre-seeded Father. Any "
        "growth indicates a false-positive role match. "
        f"Pool: {[(m.name, m.role, m.pronouns) for m in snapshot.npc_pool]}"
    )
