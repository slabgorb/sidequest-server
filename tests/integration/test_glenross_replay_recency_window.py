"""Story 49-1 VERIFY — Glenross save replay against GREEN.

Replay the 2026-05-11 Glenross playtest save to confirm the
Recency-zone window resolves the regressions surfaced that day:

1. **Father→mother gender flip.** Turn-5 narrator prose established a
   male patient ("He's through the back passage", "where Mrs. Gow laid
   him after", "a man who has done this office before"). Turn-6 prose
   invented a female patient ("the wee one's mother", "she's through
   here"). Root cause: turn 5's prose lived in the Valley-zone JSON
   dump and the narrator's attention had decayed by the time it
   composed turn 6.

2. **Secateurs set down twice.** Turn 5: "The Reverend sets the
   secateurs down on the blotter". Turn 6: "The Reverend sets the
   secateurs on the hall table". Same hands, same prop, two turns.
   Prose-only fact dropped because narrative_log was buried in the
   game_state blob.

The fix this VERIFY pins:

- session_helpers populates ``TurnContext.recent_narrative_log`` with
  ``snapshot.narrative_log[-4:]`` and strips ``narrative_log`` from the
  ``state_summary`` JSON.
- ``Orchestrator.build_narrator_prompt`` renders the window into a
  ``recent_narrative_context`` section in ``AttentionZone.Recency``.
- ``recent_narrative_context_injected`` OTEL span fires.

Skipped gracefully when the save db is not on disk so CI stays
hermetic; the test is load-bearing on Keith's machine and on any
playtester rerunning Story 49-1.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sidequest.agents.orchestrator import Orchestrator
from sidequest.agents.prompt_framework.types import AttentionZone
from sidequest.game.session import GameSnapshot, NarrativeEntry
from sidequest.genre.loader import load_genre_pack
from sidequest.server.session_handler import _build_turn_context, _SessionData
from tests._helpers.session_room import room_for

GLENROSS_SAVE = (
    Path.home() / ".sidequest" / "saves" / "games" / "2026-05-11-glenross" / "save.db"
)
CONTENT_GENRE_PACKS = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"

# The buggy turn-6 narrator prose. If this distinctive language appears
# in the post-fix prompt's Recency section, that's just the OLD entries
# riding through (it's the buggy entry that we want to ALSO catch as
# leakage). Used as a sanity guard, not the primary assertion.
TURN_6_NARRATOR_DISTINCTIVE = "the wee one's mother"

# Turn-5 narrator gender cues. These must survive into the Recency-zone
# section because the narrator built turn 6 with the log truncated to
# pre-turn-6, i.e. with turn-5 narrator entry as the most recent
# in-zone fact.
TURN_5_MALE_CUES = (
    "back passage",  # "He's through the back passage"
    "Mrs. Gow laid him",  # explicit male object pronoun
    "this office before",  # "a man who has done this office before"
)
TURN_5_PROSE_FACTS = (
    "secateurs",  # the secateurs-set-down-twice fact
    "blotter",  # specific surface — turn-6 buggy version said "hall table"
)


# ---------------------------------------------------------------------------
# Save-loader helpers
# ---------------------------------------------------------------------------


def _load_glenross_snapshot() -> GameSnapshot:
    """Read the snapshot_json from the save and validate to GameSnapshot."""
    conn = sqlite3.connect(str(GLENROSS_SAVE))
    try:
        row = conn.execute("SELECT snapshot_json FROM game_state WHERE id = 1").fetchone()
    finally:
        conn.close()
    assert row is not None, "glenross save has no game_state row"
    return GameSnapshot.model_validate(json.loads(row[0]))


def _load_narrative_log_up_to(round_inclusive: int) -> list[NarrativeEntry]:
    """Pull narrative_log rows from the save, keeping rounds ≤ N.

    Reconstructs the durable log as it stood when the narrator was
    invoked for round (N+1).
    """
    conn = sqlite3.connect(str(GLENROSS_SAVE))
    try:
        rows = conn.execute(
            """SELECT round_number, author, content, tags
               FROM narrative_log
               WHERE round_number <= ?
               ORDER BY id ASC""",
            (round_inclusive,),
        ).fetchall()
    finally:
        conn.close()
    entries: list[NarrativeEntry] = []
    for round_number, author, content, tags_json in rows:
        tags = json.loads(tags_json) if tags_json else []
        entries.append(
            NarrativeEntry(round=round_number, author=author, content=content, tags=tags)
        )
    return entries


def _glenross_session_data(snapshot: GameSnapshot, *, player_name: str) -> _SessionData:
    pack = load_genre_pack(CONTENT_GENRE_PACKS / snapshot.genre_slug)
    sd = _SessionData(
        genre_slug=snapshot.genre_slug,
        world_slug=snapshot.world_slug,
        player_name=player_name,
        player_id=f"player:{player_name.lower()}",
        snapshot=snapshot,
        store=MagicMock(),
        genre_pack=pack,
        orchestrator=MagicMock(),
    )
    return sd


# ---------------------------------------------------------------------------
# Skip-on-missing guard — CI stays hermetic; local replay is the use case
# ---------------------------------------------------------------------------


pytestmark = [
    pytest.mark.skipif(
        not GLENROSS_SAVE.exists(),
        reason=(
            f"glenross save not present at {GLENROSS_SAVE}; this is the live "
            "replay test from Story 49-1 and runs on Keith's machine"
        ),
    ),
    pytest.mark.skipif(
        not (CONTENT_GENRE_PACKS / "victoria").exists(),
        reason="sidequest-content/genre_packs/victoria not checked out",
    ),
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# AC #6: replay turn 6 — turn-5 male cues must reach the Recency section
# ---------------------------------------------------------------------------


async def test_glenross_turn6_replay_male_cues_reach_recency_zone():
    """Replay turn 6 of the 2026-05-11 Glenross save against GREEN.

    Reconstruct the narrative_log as it stood *just before* the narrator
    composed turn 6 (i.e. rounds 1..5 plus the round-6 player action;
    round-6 narrator entry excluded). The composed prompt's
    ``recent_narrative_context`` section MUST carry turn 5's male
    pronouns and the secateurs-on-the-blotter fact — the precondition
    for the narrator not flipping to "mother/she" or setting the
    secateurs down again.

    This is not a behavioral assertion against the LLM (we don't replay
    Claude); it's a precondition assertion against the prompt. If the
    male cues are absent from the high-attention zone, no narrator —
    even a good one — has the visible context to refuse the flip.
    """
    snapshot = _load_glenross_snapshot()

    # Reconstruct the pre-turn-6 narrative log (rounds 1..5 narrator/
    # player entries plus the round-6 player action that triggered the
    # narrator turn). The narrative_log table holds the full session;
    # we filter to rounds <= 6 and then drop the round-6 narrator entry
    # since that's what the narrator was about to produce.
    full_log = _load_narrative_log_up_to(round_inclusive=6)
    pre_turn6_log = [e for e in full_log if not (e.round == 6 and e.author == "narrator")]
    assert pre_turn6_log, "glenross save lacks narrative_log rows for rounds ≤ 6"
    assert any(e.round == 5 and e.author == "narrator" for e in pre_turn6_log), (
        "glenross save is missing the turn-5 narrator entry that carries the "
        "male-coded prose this replay tests against"
    )

    snapshot.narrative_log = pre_turn6_log
    sd = _glenross_session_data(snapshot, player_name="Ziggy")
    sd._room = room_for(snapshot, slug=snapshot.world_slug)

    ctx = _build_turn_context(sd, room=sd._room)

    # Wiring check: session_helpers populated the recency-window field.
    assert ctx.recent_narrative_log, (
        "TurnContext.recent_narrative_log is empty after replay — wiring "
        "from snapshot.narrative_log[-4:] failed"
    )
    assert len(ctx.recent_narrative_log) == 4, (
        f"expected 4-entry window; got {len(ctx.recent_narrative_log)}"
    )

    orch = Orchestrator()
    prompt_text, registry = await orch.build_narrator_prompt(
        "Ziggy: I follow the reverend to what is apparently my newest patient.",
        ctx,
    )

    sections = [
        s for s in registry.registry(orch._narrator.name()) if s.name == "recent_narrative_context"
    ]
    assert len(sections) == 1, (
        f"expected recent_narrative_context section in Recency zone; "
        f"found {[s.name for s in registry.registry(orch._narrator.name()) if s.zone == AttentionZone.Recency]}"
    )
    section = sections[0]
    assert section.zone == AttentionZone.Recency

    body = section.content

    # AC #6 primary assertion: turn-5 male cues survive into the prompt.
    for cue in TURN_5_MALE_CUES:
        assert cue in body, (
            f"turn-5 male cue {cue!r} missing from recent_narrative_context — "
            "narrator would not see male framing and could re-invent 'mother'"
        )

    # Secateurs-set-down-twice secondary regression: the prose-only fact
    # must also survive in-zone so the narrator cannot drop the same prop
    # a second time.
    for fact in TURN_5_PROSE_FACTS:
        assert fact in body, (
            f"turn-5 prose-only fact {fact!r} missing from "
            "recent_narrative_context — secateurs-set-down-twice regression "
            "could recur"
        )

    # The composed prompt text must surface the section too (registry +
    # compose wiring). Pinning one distinctive male phrase is sufficient.
    assert "Mrs. Gow laid him" in prompt_text, (
        "recent_narrative_context section is registered but its content "
        "did not flow into the composed prompt_text — compose-stage wiring broken"
    )


async def test_glenross_replay_strips_narrative_log_from_state_summary():
    """AC #3 on real data: ``state_summary`` JSON dump must NOT carry the
    full narrative_log on the Glenross replay. Without this, the male
    cues that just landed in the Recency section ALSO ride in the
    Valley-zone dump — the duplicate drowns the high-attention copy
    with decayed noise."""
    snapshot = _load_glenross_snapshot()
    snapshot.narrative_log = _load_narrative_log_up_to(round_inclusive=6)
    sd = _glenross_session_data(snapshot, player_name="Ziggy")
    sd._room = room_for(snapshot, slug=snapshot.world_slug)

    ctx = _build_turn_context(sd, room=sd._room)
    assert ctx.state_summary is not None

    payload = json.loads(ctx.state_summary)
    entries_in_state = payload.get("narrative_log", [])
    assert len(entries_in_state) <= 1, (
        f"state_summary still carries {len(entries_in_state)} narrative_log "
        "entries on the Glenross replay — the Valley-zone duplicate was not "
        "stripped, the same disease that broke this playtest will recur."
    )

    # Distinctive multi-word turn-5 narrator prose fragments — long enough to
    # be unique to the narrative_log entry (single words like "secateurs"
    # also live in NPC ``appearance`` fields on the canon-poisoned snapshot
    # and would false-positive a substring check).
    turn_5_unique_fragments = (
        "sets the secateurs down on the blotter",
        "shrugging into it with the slow economy of a man",
    )
    for fragment in turn_5_unique_fragments:
        assert fragment not in ctx.state_summary, (
            f"turn-5 narrator prose fragment {fragment!r} still dumped via "
            "state_summary JSON in the Valley zone — Recency injection alone "
            "is not enough; the duplicate must be stripped."
        )


# ---------------------------------------------------------------------------
# OTEL: the prompt-assembly span fires on a real replay
# ---------------------------------------------------------------------------


async def test_glenross_replay_emits_recent_narrative_context_injected_span(otel_capture):
    """Per CLAUDE.md OTEL principle: the prompt-assembly subsystem must
    emit observable spans so Sebastien's GM panel can audit whether the
    injector actually engaged. Verify on real save data, not just on
    synthetic fixtures."""
    snapshot = _load_glenross_snapshot()
    snapshot.narrative_log = _load_narrative_log_up_to(round_inclusive=6)
    sd = _glenross_session_data(snapshot, player_name="Ziggy")
    sd._room = room_for(snapshot, slug=snapshot.world_slug)

    ctx = _build_turn_context(sd, room=sd._room)

    orch = Orchestrator()
    await orch.build_narrator_prompt(
        "Ziggy: I follow the reverend to what is apparently my newest patient.",
        ctx,
    )

    spans = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == "recent_narrative_context_injected"
    ]
    assert len(spans) == 1, (
        f"expected exactly one recent_narrative_context_injected span on "
        f"Glenross replay; got {[s.name for s in otel_capture.get_finished_spans()]}"
    )
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("turn_count") == 4, (
        f"replay window should be 4 entries; got {attrs.get('turn_count')}"
    )
    assert isinstance(attrs.get("total_tokens"), int)
    assert attrs["total_tokens"] > 0


# ---------------------------------------------------------------------------
# Negative sanity guard
# ---------------------------------------------------------------------------


async def test_glenross_replay_does_not_leak_turn6_buggy_narrator_entry():
    """The reconstructed pre-turn-6 log MUST exclude the round-6 narrator
    entry — that's the buggy "mother/she" prose we don't want in the
    Recency window (it would amount to feeding the buggy answer back as
    canon).

    Guards the replay fixture itself from drifting silently.
    """
    snapshot = _load_glenross_snapshot()
    snapshot.narrative_log = [
        e
        for e in _load_narrative_log_up_to(round_inclusive=6)
        if not (e.round == 6 and e.author == "narrator")
    ]
    sd = _glenross_session_data(snapshot, player_name="Ziggy")
    sd._room = room_for(snapshot, slug=snapshot.world_slug)

    ctx = _build_turn_context(sd, room=sd._room)

    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("Ziggy: pre-turn-6 setup", ctx)
    sections = [
        s for s in registry.registry(orch._narrator.name()) if s.name == "recent_narrative_context"
    ]
    assert len(sections) == 1
    assert TURN_6_NARRATOR_DISTINCTIVE not in sections[0].content, (
        "buggy turn-6 narrator entry leaked into the replay window — the "
        "test would be tautologically green (we'd be feeding the bug back "
        "to the narrator). Fix the fixture filter, not the assertion."
    )
