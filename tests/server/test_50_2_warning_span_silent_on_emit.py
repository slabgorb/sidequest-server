"""Story 50-2 — wiring RED tests for the warning-span lifecycle flip.

After 50-2 lands, the OTEL warning span
``confrontation.skipped_with_trigger_keywords`` must flip from
steady-state ("narrator keeps describing chases without firing them")
to regression-detector ("only fires on residual edge cases").

These tests pin the post-fix steady-state:
- When the narrator EMITS a non-null ``confrontation`` field on prose
  that contains trigger keywords, the encounter MUST instantiate AND
  the warning span MUST NOT fire.
- The matrix covers every confrontation type the test fixture pack
  declares (combat, chase, negotiation) crossed with canonical
  trigger-keyword prose drawn from the 2026-04-26 archive plus a
  Victoria-shaped social fixture for negotiation.

Note: the four Victoria social types (scandal, social_duel, trial,
auction) are pinned via prompt-content tests in
``test_50_2_confrontation_trigger_prompt.py`` rather than fixture-pack
expansion, because the apply-narration path is type-agnostic — it
routes any pack-declared type through the same dispatch. Type-name
parity is enforced by the prompt tests; this file covers the wiring.

Scope guardrails (per session SM Assessment):
- No keyword-list editing in ``_CONFRONTATION_TRIGGER_PATTERNS``.
- No new server-side keyword→instantiation path (that would be a
  silent fallback per CLAUDE.md).
- The fix is the prompt; these tests verify the wire is already in
  place to receive a corrected emit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult, NpcMention
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import load_genre_pack
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from tests._helpers.session_room import room_for

_FIXTURE_PACK = Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "test_genre"


@pytest.fixture
def fixture_snap():
    """Snapshot + pack pair using the fixture genre (combat / chase /
    negotiation confrontations defined).
    """
    snap = GameSnapshot(genre="caverns_and_claudes")
    pack = load_genre_pack(_FIXTURE_PACK)
    return snap, pack


# Canonical trigger-keyword prose for each fixture-pack type. The chase
# fixture is the 2026-04-26 archive prose verbatim — the one PR #177's
# warning span surfaced. The combat fixture uses two regex labels
# (``weapons hot``, ``opens fire``). The negotiation fixture uses
# bargaining language that the schema's TRIGGER CRITERIA names.
_CANONICAL_FIXTURES: tuple[tuple[str, str, str], ...] = (
    (
        "chase",
        # From sq-playtest-pingpong.archive-20260506-074557.md, turn 20.
        "The patrol cutter is spinning her reactor up from cold-soak, "
        "asking the tower for permission to engage. The pursuit is on.",
        "Patrol cutter",
    ),
    (
        "combat",
        "The bandit draws a knife and opens fire, weapons hot.",
        "Bandit",
    ),
    (
        "negotiation",
        "The merchant names a price for the salvaged datapad and waits for your counter-offer.",
        "Merchant",
    ),
)


@pytest.mark.parametrize(
    ("encounter_type", "narration", "opponent_name"),
    _CANONICAL_FIXTURES,
    ids=[c[0] for c in _CANONICAL_FIXTURES],
)
def test_warning_span_silent_when_narrator_emits_confrontation_on_trigger_prose(
    fixture_snap,
    monkeypatch,
    encounter_type: str,
    narration: str,
    opponent_name: str,
) -> None:
    """Post-fix steady state: trigger-keyword prose + emitted
    ``confrontation`` field → encounter instantiates, NO warning span.

    This is the test the GM panel uses to verify the fix is live. Pre-fix
    (PR #177 baseline) the narrator routinely emits ``confrontation=None``
    on this same prose, the warning fires, and the encounter is never
    created. Post-fix the narrator should emit a non-null type — and
    when it does, the warning must stay silent so the GM panel can use
    the span as a regression detector for residual cases.

    Test setup: simulate the post-fix world by providing a populated
    ``confrontation`` field on every canonical-prose fixture. The wire
    must already exist to (a) instantiate and (b) NOT fire the warning.
    If either fails, the warning-span lifecycle flip is broken.
    """
    captured: list[tuple[str, dict, dict]] = []

    def fake_publish(event_type, fields, *, component="", severity="info"):
        captured.append((event_type, fields, {"component": component, "severity": severity}))

    import sidequest.server.narration_apply as _napply

    monkeypatch.setattr(_napply, "_watcher_publish", fake_publish)

    snap, pack = fixture_snap
    snap.genre_slug = "caverns_and_claudes"
    result = NarrationTurnResult(
        narration=narration,
        confrontation=encounter_type,
        npcs_present=[
            NpcMention(name=opponent_name, role="hostile", is_new=True),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Itchy",
        pack=pack,
        room=room_for(snap),
    )

    # (1) Encounter instantiated end-to-end — this is the AC's
    #     "instantiated server-side on receipt of that block" line.
    assert snap.encounter is not None, (
        f"Trigger prose with confrontation={encounter_type!r} must instantiate "
        f"an encounter via the existing dispatch path. Got snap.encounter=None."
    )
    assert snap.encounter.encounter_type == encounter_type

    # (2) Warning span MUST stay silent — the regression-detector role
    #     requires that emitted-confrontation turns never trip it.
    skipped = [
        (fields, meta)
        for et, fields, meta in captured
        if et == "state_transition" and fields.get("op") == "skipped_with_trigger_keywords"
    ]
    assert not skipped, (
        f"confrontation.skipped_with_trigger_keywords fired on a turn where "
        f"the narrator emitted confrontation={encounter_type!r}. Post-fix the "
        f"warning must be scoped to confrontation=None turns only — otherwise "
        f"the GM panel can't distinguish 'narrator drift regression' from "
        f"'system working as designed'."
    )


def test_archive_chase_fixture_pre_fix_baseline_still_warns(fixture_snap, monkeypatch) -> None:
    """Regression-detector half of the AC: the warning span continues
    to fire on the archive's canonical PRE-FIX prose pattern —
    ``confrontation=None`` with trigger keywords in prose. The fix
    closes the gap by making the narrator EMIT a confrontation, not by
    silencing the detector.

    This test exists to guard against an accidental "fix" that disables
    the warning span entirely. The span must remain live so future
    drift surfaces immediately.
    """
    captured: list[tuple[str, dict, dict]] = []

    def fake_publish(event_type, fields, *, component="", severity="info"):
        captured.append((event_type, fields, {"component": component, "severity": severity}))

    import sidequest.server.narration_apply as _napply

    monkeypatch.setattr(_napply, "_watcher_publish", fake_publish)

    snap, pack = fixture_snap
    snap.genre_slug = "caverns_and_claudes"
    # Same archive prose as the chase fixture above, but with the
    # pre-fix narrator behavior (confrontation=None).
    result = NarrationTurnResult(
        narration=(
            "The patrol cutter is spinning her reactor up from cold-soak, "
            "asking the tower for permission to engage."
        ),
        confrontation=None,
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Itchy",
        pack=pack,
        room=room_for(snap),
    )

    skipped = [
        fields
        for et, fields, _ in captured
        if et == "state_transition" and fields.get("op") == "skipped_with_trigger_keywords"
    ]
    assert skipped, (
        "Warning span must continue firing on PRE-FIX prose (confrontation=None "
        "+ trigger keywords) — the fix is to make the narrator EMIT, not to "
        "silence the detector. Disabling the span would lose the regression "
        "channel for future narrator drift."
    )
    # No encounter — the architectural commitment is narrator emission;
    # the server does NOT auto-fire on prose inference.
    assert snap.encounter is None
