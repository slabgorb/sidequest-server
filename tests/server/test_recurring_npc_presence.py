"""Failing tests for Story 45-53 — Recurring NPC presence detector.

The narrator's npcs_met emission rule (story 45-53) is paired with a
server-side detector that catches the failure mode CLAUDE.md's "No Silent
Fallback" principle prohibits: when narrator prose names a known recurring
NPC by name as onstage but ``npcs_present`` does not include them, the
detector must emit a warning span so Sebastien's GM-panel lie-detector can
see the gap (and over time the narrator's prompt or the prompt-injection
layer can correct).

Detector contract (to be implemented in ``sidequest/server/session_helpers.py``
and called from ``sidequest/server/narration_apply.py``):

    def _detect_missed_recurring_npcs(
        *,
        snapshot: GameSnapshot,
        narration_text: str,
        emitted_mentions: list[NpcMention],
        turn_num: int,
    ) -> None:
        '''Scan narration prose for known recurring NPC names that are not
        in emitted_mentions. For each miss, emit
        SPAN_NPC_RECURRING_PRESENCE_MISSED with attributes npc_name, source
        ("npcs"|"npc_pool"), turn_number, last_seen_turn. Match is
        word-boundary case-insensitive on the name. PC names are excluded.
        '''

OTEL contract:
- New span ``SPAN_NPC_RECURRING_PRESENCE_MISSED = "npc.recurring_presence_missed"``
  in ``sidequest/telemetry/spans/npc.py`` with a ``SpanRoute`` so
  GameWatcher emits a ``state_transition`` event with
  ``op="recurring_presence_missed"`` and ``component="npc_registry"``.
- Helper context manager ``npc_recurring_presence_missed_span(...)``
  exported from ``sidequest.telemetry.spans``.

Wiring contract:
- ``_apply_narration_result_to_snapshot`` must invoke the detector after
  ``_apply_npc_mentions``. The wiring test exercises the full path:
  GameSnapshot pre-seeded with a stateful Npc, a NarrationTurnResult whose
  prose names that NPC by name but whose ``npcs_present`` is empty, applied
  through ``_apply_narration_result_to_snapshot`` → span captured.

Related: ADR-031 Game Watcher / OTEL, CLAUDE.md OTEL Observability Principle.
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
# Helpers
# ---------------------------------------------------------------------------


def _core(name: str) -> CreatureCore:
    return CreatureCore(name=name, description="X.", personality="Y.")


def _result(narration: str, npcs_present: list[NpcMention] | None = None) -> NarrationTurnResult:
    return NarrationTurnResult(
        narration=narration,
        npcs_present=list(npcs_present or []),
        is_degraded=False,
    )


SPAN_NAME = "npc.recurring_presence_missed"


def _missed_spans(
    otel_capture: InMemorySpanExporter,
    expected_name: str | None = None,
) -> list:
    """Filter captured spans by name (and optional npc_name attribute)."""
    spans = [s for s in otel_capture.get_finished_spans() if s.name == SPAN_NAME]
    if expected_name is not None:
        spans = [s for s in spans if (s.attributes or {}).get("npc_name") == expected_name]
    return spans


# ---------------------------------------------------------------------------
# Span-catalog tests (AC3 — OTEL contract)
# ---------------------------------------------------------------------------


def test_span_npc_recurring_presence_missed_is_defined_in_catalog():
    """Per the OTEL Observability Principle, the recurring-presence detector
    must register a stable span name in the telemetry catalog so the GM
    panel and routing tests see it. The constant must be exactly
    'npc.recurring_presence_missed'.
    """
    from sidequest.telemetry import spans as spans_module

    assert hasattr(spans_module, "SPAN_NPC_RECURRING_PRESENCE_MISSED"), (
        "SPAN_NPC_RECURRING_PRESENCE_MISSED missing from telemetry catalog "
        "— without it the GM panel can't tell whether the recurring-NPC "
        "detector engaged this turn."
    )
    assert spans_module.SPAN_NPC_RECURRING_PRESENCE_MISSED == SPAN_NAME, (
        f"Span name must be exactly {SPAN_NAME!r} for the GM panel filter to match."
    )


def test_npc_recurring_presence_missed_span_is_routed():
    """Every live span must be either routed (in SPAN_ROUTES) or flat-only
    (in FLAT_ONLY_SPANS). Recurring-presence-missed is a state_transition
    event under the npc_registry component (parallel to npc.referenced).
    """
    from sidequest.telemetry.spans import SPAN_ROUTES

    assert SPAN_NAME in SPAN_ROUTES, (
        f"{SPAN_NAME!r} not in SPAN_ROUTES — GameWatcher will drop it on "
        "the floor and the GM panel will never receive the event."
    )
    route = SPAN_ROUTES[SPAN_NAME]
    assert route.event_type == "state_transition", (
        "Recurring-presence misses must route as state_transition events so "
        "the GM panel renders them under the same lane as npc.referenced."
    )
    assert route.component == "npc_registry", (
        "Component must be 'npc_registry' to share the GM-panel column with "
        "other NPC-state spans (auto_registered, referenced, reinvented)."
    )


def test_npc_recurring_presence_missed_span_helper_is_exported():
    """The context-manager helper must be exported from
    ``sidequest.telemetry.spans`` so production code can ``from
    sidequest.telemetry.spans import npc_recurring_presence_missed_span``
    without reaching into the submodule.
    """
    from sidequest.telemetry import spans as spans_module

    assert hasattr(spans_module, "npc_recurring_presence_missed_span"), (
        "npc_recurring_presence_missed_span helper must be exported from "
        "sidequest.telemetry.spans (parallel to npc_referenced_span)."
    )


# ---------------------------------------------------------------------------
# Detector tests (AC3 — no silent fallback)
# ---------------------------------------------------------------------------


def test_detector_warns_when_known_npc_named_in_prose_but_missing_from_npcs_present(
    otel_capture,
):
    """A stateful Npc named 'Boris' is on the snapshot. The narrator's prose
    names Boris ('Boris pours another round.') but ``npcs_present`` is
    empty. The detector must emit one warning span for Boris with
    source='npcs'.
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(npcs=[Npc(core=_core("Boris"), last_seen_turn=4)])

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="Boris pours another round and slides it across.",
        emitted_mentions=[],
        turn_num=5,
    )

    spans = _missed_spans(otel_capture, expected_name="Boris")
    assert len(spans) == 1, (
        "Detector must emit exactly one span when a known stateful Npc is "
        "named in prose but missing from npcs_present."
    )
    attrs = spans[0].attributes or {}
    assert attrs.get("source") == "npcs", (
        "Span source must be 'npcs' for stateful Npc misses (vs 'npc_pool' "
        "for pool-member misses) so the GM panel can tell which store the "
        "NPC came from."
    )
    assert attrs.get("turn_number") == 5
    assert attrs.get("last_seen_turn") == 4, (
        "last_seen_turn must propagate from the Npc record so the GM panel "
        "can show 'last present 1 turn ago' in the missed-presence row."
    )


def test_detector_warns_when_known_pool_member_named_in_prose_but_missing(
    otel_capture,
):
    """Pool members count as 'known recurring NPCs' too — the AC enumerates
    allies, merchants, quest-givers, and named bystanders. A pool member
    named in prose but absent from npcs_present must trigger the same
    warning, with source='npc_pool'.
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(
        npc_pool=[NpcPoolMember(name="Marya", role="merchant", drawn_from="legacy_registry")]
    )

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="Marya is bent over her ledger, ignoring you.",
        emitted_mentions=[],
        turn_num=12,
    )

    spans = _missed_spans(otel_capture, expected_name="Marya")
    assert len(spans) == 1, (
        "Pool-member misses must also fire — the AC's NPC-type coverage "
        "(merchant, quest-giver, ally, bystander) lives primarily in the "
        "pool, not in npcs."
    )
    attrs = spans[0].attributes or {}
    assert attrs.get("source") == "npc_pool"
    assert attrs.get("turn_number") == 12
    # Pool-only members carry no last_seen_turn (the field lives on Npc, not
    # NpcPoolMember). Detector must fall back to 0 — asserting this nails
    # down the contract so the GM panel can render "last present —"
    # unambiguously for pool-only misses.
    assert attrs.get("last_seen_turn") == 0


def test_detector_silent_when_known_npc_is_emitted_in_npcs_present(otel_capture):
    """Happy path. Boris is named in prose AND emitted in npcs_present —
    no warning span should fire. This is the contract the narrator
    satisfies after the prompt amendment lands.
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(npcs=[Npc(core=_core("Boris"))])

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="Boris pours another round.",
        emitted_mentions=[NpcMention(name="Boris", role="barkeep")],
        turn_num=5,
    )

    assert _missed_spans(otel_capture) == [], (
        "Detector must NOT warn when the narrator emitted the recurring "
        "NPC. False positives turn the GM panel into noise and the lie-"
        "detector loses its signal."
    )


def test_detector_silent_when_unknown_name_appears_in_prose(otel_capture):
    """The detector only fires for *known* recurring NPCs — names already
    in snapshot.npcs or snapshot.npc_pool. A novel name in prose is
    handled by the narrator-invented branch in _apply_npc_mentions and
    must NOT be treated as a missed recurring presence.
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot()  # empty npcs and npc_pool

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="A stranger named Kara approaches the bar.",
        emitted_mentions=[],
        turn_num=1,
    )

    assert _missed_spans(otel_capture) == [], (
        "Novel names must not trigger the recurring-presence detector — "
        "only names already in the npcs or npc_pool stores are 'known "
        "recurring NPCs' for purposes of this rule."
    )


def test_detector_match_is_case_insensitive(otel_capture):
    """Name match must be case-insensitive — narrator prose may capitalize
    a name differently across turns. ``_apply_npc_mentions`` already uses
    case-folded comparison; the detector must match that contract or it
    will create a false negative ("BORIS" in prose but pool stores
    "Boris" → no warning even though it should fire).
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(npcs=[Npc(core=_core("Boris"))])

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="BORIS slams the door.",
        emitted_mentions=[],
        turn_num=2,
    )

    assert len(_missed_spans(otel_capture, expected_name="Boris")) == 1


def test_detector_matches_on_word_boundary_not_substring(otel_capture):
    """A bare substring match would false-positive on names that are a
    prefix of a longer word. 'Marya' in 'Maryana' should not trigger; the
    detector must use word-boundary semantics (e.g. regex \\bname\\b on
    case-folded text). Without this guard the GM panel fills with phantom
    misses every time a similar-sounding name appears.
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(npc_pool=[NpcPoolMember(name="Marya", drawn_from="legacy_registry")])

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="Maryana cleans the counter, humming.",
        emitted_mentions=[],
        turn_num=3,
    )

    assert _missed_spans(otel_capture) == [], (
        "Substring match on 'Marya' inside 'Maryana' must NOT fire — use word-boundary semantics."
    )


def test_detector_skips_pc_names(otel_capture):
    """PC names sometimes appear in narration (the narrator describing the
    party), but PCs are not NPCs. The detector must exclude PC names so
    the warning never fires on a PC even if a PC's name happens to also
    be in npc_pool (corrupted-state scenario).
    """
    from sidequest.game.character import Character
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    pc = Character(
        core=_core("Rux"),
        backstory="A wanderer.",
        char_class="adventurer",
        race="human",
    )
    # Edge case: Rux in npc_pool too (corrupted state) — PC filter must
    # still skip the name.
    snapshot = GameSnapshot(
        characters=[pc],
        npc_pool=[NpcPoolMember(name="Rux", drawn_from="legacy_registry")],
    )

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="Rux glances around the chamber.",
        emitted_mentions=[],
        turn_num=4,
    )

    assert _missed_spans(otel_capture) == [], (
        "PC names must be filtered out of the recurring-presence check — "
        "the narrator naming a PC is not an NPC presence miss."
    )


def test_detector_npcs_lookup_shadows_pool_member_with_same_name(otel_capture):
    """If a name lives in BOTH npcs (stateful) and npc_pool, only one
    miss span should fire — and source must be 'npcs' (the stateful
    record wins). This mirrors the existing ``_apply_npc_mentions``
    shadow rule and prevents a duplicate emission turning into double
    GM-panel rows.
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(
        npcs=[Npc(core=_core("Boris"), last_seen_turn=7)],
        npc_pool=[NpcPoolMember(name="Boris", drawn_from="legacy_registry")],
    )

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="Boris waves you over.",
        emitted_mentions=[],
        turn_num=8,
    )

    spans = _missed_spans(otel_capture, expected_name="Boris")
    assert len(spans) == 1, (
        "Duplicate names across npcs and npc_pool must produce ONE miss "
        "span, not two — npcs lookup shadows pool lookup."
    )
    assert (spans[0].attributes or {}).get("source") == "npcs"


def test_detector_handles_multiple_misses_in_one_call(otel_capture):
    """Two known recurring NPCs both named in prose, both omitted from
    npcs_present — two distinct spans must fire (one per missed name).
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(
        npcs=[Npc(core=_core("Boris"))],
        npc_pool=[NpcPoolMember(name="Marya", drawn_from="legacy_registry")],
    )

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="Boris and Marya argue over the price of the bottle.",
        emitted_mentions=[],
        turn_num=9,
    )

    boris_spans = _missed_spans(otel_capture, expected_name="Boris")
    marya_spans = _missed_spans(otel_capture, expected_name="Marya")
    assert len(boris_spans) == 1
    assert len(marya_spans) == 1


def test_detector_logs_warning_with_descriptive_message(caplog):
    """AC3 explicit text: 'the test must fail with a clear error message
    indicating which NPC was missed'. The detector must log at WARNING
    level (rule #4 — client-side correctness gap, not server error) and
    the log record must name the missed NPC so a developer reading
    /tmp/sidequest-server.log can identify which character vanished.
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(npcs=[Npc(core=_core("Boris"))])

    with caplog.at_level(logging.WARNING):
        _detect_missed_recurring_npcs(
            snapshot=snapshot,
            narration_text="Boris pours another round.",
            emitted_mentions=[],
            turn_num=5,
        )

    matched = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "Boris" in rec.getMessage()
    ]
    assert matched, (
        "Detector must emit a WARNING-level log naming the missed NPC. "
        "Without it the only signal is the OTEL span; developers reading "
        "the raw log file should see the miss too."
    )


# ---------------------------------------------------------------------------
# Wiring test — the detector is reachable from the production apply path.
# CLAUDE.md "Every Test Suite Needs a Wiring Test" mandate.
# ---------------------------------------------------------------------------


def test_wiring_apply_narration_result_invokes_recurring_presence_detector(
    otel_capture,
):
    """Integration: the detector must be wired into
    ``_apply_narration_result_to_snapshot`` so production turns exercise
    it without an explicit caller. A pre-seeded Npc plus a narration
    result whose prose names that NPC but whose npcs_present is empty
    must produce the warning span end-to-end through the apply pipeline.

    This is the wire-first guard. Unit tests on the detector alone do
    NOT prove the production code path engages it — only this test does.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snapshot = GameSnapshot(
        genre_slug="test",
        world_slug="test",
        npcs=[Npc(core=_core("Boris"), last_seen_turn=4)],
    )
    snapshot.character_locations["Hero"] = "Tavern"

    result = _result(
        narration="Boris pours another round and slides it across the bar.",
        npcs_present=[],  # the bug: narrator forgot to emit Boris
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        "player",
        room=room_for(snapshot),
        acting_character_name="Hero",
    )

    spans = _missed_spans(otel_capture, expected_name="Boris")
    assert len(spans) == 1, (
        "Wiring failure: _apply_narration_result_to_snapshot must invoke "
        "_detect_missed_recurring_npcs so production turns surface miss "
        "events to the GM panel. Detector unit tests passed but the "
        "production path does not call the detector — exactly the failure "
        "mode CLAUDE.md's 'Verify Wiring, Not Just Existence' principle "
        "warns about."
    )


def test_wiring_apply_narration_result_silent_when_recurring_npc_emitted(
    otel_capture,
):
    """The wiring path must NOT false-positive when the narrator does the
    right thing. Boris in prose AND in npcs_present: zero miss spans
    end-to-end.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snapshot = GameSnapshot(
        genre_slug="test",
        world_slug="test",
        npcs=[Npc(core=_core("Boris"))],
    )
    snapshot.character_locations["Hero"] = "Tavern"

    result = _result(
        narration="Boris pours another round.",
        npcs_present=[NpcMention(name="Boris", role="barkeep")],
    )

    _apply_narration_result_to_snapshot(
        snapshot,
        result,
        "player",
        room=room_for(snapshot),
        acting_character_name="Hero",
    )

    assert _missed_spans(otel_capture) == [], (
        "End-to-end happy path: when the narrator emits the recurring NPC "
        "in npcs_present, no miss span fires."
    )


# ---------------------------------------------------------------------------
# Span-routing completeness — guards the existing test_routing_completeness.
# ---------------------------------------------------------------------------


def test_recurring_presence_missed_span_attributes_round_trip_via_route():
    """The SpanRoute extract function must surface the four attributes the
    GM panel needs: name, source, turn_number, last_seen_turn. We feed
    a minimal stub through the extract lambda and verify the dict shape.
    """
    from sidequest.telemetry.spans import SPAN_ROUTES

    pytest.importorskip("opentelemetry")
    route = SPAN_ROUTES[SPAN_NAME]

    class _Stub:
        attributes = {
            "npc_name": "Boris",
            "source": "npcs",
            "turn_number": 5,
            "last_seen_turn": 4,
        }

    extracted = route.extract(_Stub())
    assert extracted["name"] == "Boris"
    assert extracted["source"] == "npcs"
    assert extracted["turn_number"] == 5
    assert extracted["last_seen_turn"] == 4
    assert extracted["op"] == "recurring_presence_missed", (
        "Route op must be 'recurring_presence_missed' so the GM panel can "
        "filter on it without inspecting the span name string directly."
    )
    # ``field`` mirrors ``source`` so the GM panel filters route stateful
    # Npc misses to the npc_registry column (parallel to npc.auto_registered)
    # and pool-only misses to the npc_pool column (parallel to npc.referenced).
    assert extracted["field"] == "npc_registry"


def test_recurring_presence_missed_span_route_field_uses_npc_pool_for_pool_source():
    """Mirror of the npcs-source field test — when ``source="npc_pool"`` the
    extracted ``field`` must be ``"npc_pool"`` so the GM panel renders the
    miss in the correct column. Without this guard the route lambda could
    silently regress to a hardcoded value.
    """
    from sidequest.telemetry.spans import SPAN_ROUTES

    route = SPAN_ROUTES[SPAN_NAME]

    class _Stub:
        attributes = {
            "npc_name": "Marya",
            "source": "npc_pool",
            "turn_number": 9,
            "last_seen_turn": 0,
        }

    extracted = route.extract(_Stub())
    assert extracted["field"] == "npc_pool"
    assert extracted["source"] == "npc_pool"


def test_detector_matches_name_with_regex_metacharacters(otel_capture):
    """``re.escape`` must guard the regex construction in the detector — NPC
    names containing characters like ``.``, ``+``, ``(``, ``)``, ``*`` must
    match literally, not as regex metacharacters. Without ``re.escape`` the
    pattern build either throws ``re.error`` or matches the wrong text.

    Concrete name: "Dr. Smith" — the ``.`` is a regex metacharacter that
    would match ANY character without escaping.
    """
    from sidequest.server.session_helpers import _detect_missed_recurring_npcs

    snapshot = GameSnapshot(
        npc_pool=[NpcPoolMember(name="Dr. Smith", role="patron", drawn_from="legacy_registry")]
    )

    _detect_missed_recurring_npcs(
        snapshot=snapshot,
        narration_text="Dr. Smith examines the wound carefully.",
        emitted_mentions=[],
        turn_num=4,
    )

    spans = _missed_spans(otel_capture, expected_name="Dr. Smith")
    assert len(spans) == 1, (
        "Detector must match names containing regex metacharacters literally "
        "via re.escape — without it 'Dr. Smith' either crashes the regex or "
        "false-matches on 'DrxSmith' / 'Drosmith'."
    )
