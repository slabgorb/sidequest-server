"""Tests for status clearing — the missing half of the condition lifecycle.

Playtest 2026-04-26 Bug #1: conditions never cleared, only accumulated.
This module exercises:

  - ``clear_scratch_on_scene_end``: severity-tier filtering (Scratch
    sweeps; Wound/Scar persist).
  - ``apply_explicit_status_clears``: narrator-emitted clear entries.
  - The wiring tests at the bottom prove the production paths
    (``_apply_narration_result_to_snapshot`` + dice resolve in
    ``session_handler``) actually call the helpers — per CLAUDE.md
    "every test suite needs a wiring test".
"""

from __future__ import annotations

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.status import Status, StatusSeverity
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from sidequest.server.status_clear import (
    apply_explicit_status_clears,
    clear_scratch_on_scene_end,
)
from tests._helpers.session_room import room_for

# ---------------------------------------------------------------------------
# Unit tests — clear_scratch_on_scene_end
# ---------------------------------------------------------------------------


def _add_status(char, text: str, severity: StatusSeverity) -> None:
    char.core.statuses.append(
        Status(
            text=text,
            severity=severity,
            absorbed_shifts=0,
            created_turn=0,
            created_in_encounter=None,
        ),
    )


def test_clear_scratch_sweeps_scratch_and_boon_only(
    snapshot_with_pack,
    character_named_sam,
):
    """Scene-bounded severities (Scratch + Boon) sweep; Wound/Scar persist.

    Boon was added 2026-04-30 — it joins Scratch in the scene-end sweep
    because temporary buffs from a working/potion/scroll are scene-scoped
    by design. A "Heightened Perception" buff from a potion shouldn't
    trail the party into the next encounter.
    """
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Choked", StatusSeverity.Scratch)
    _add_status(sam, "Bruised Ribs", StatusSeverity.Wound)
    _add_status(sam, "Marked by the Butcher", StatusSeverity.Scar)
    _add_status(sam, "Heightened Perception (3 rounds)", StatusSeverity.Boon)

    cleared = clear_scratch_on_scene_end(snap, reason="scene_end", turn=3)

    assert cleared == 2
    remaining = [s.text for s in sam.core.statuses]
    assert "Choked" not in remaining
    assert "Heightened Perception (3 rounds)" not in remaining
    assert "Bruised Ribs" in remaining
    assert "Marked by the Butcher" in remaining


def test_clear_scratch_no_op_with_empty_party(snapshot_with_pack):
    snap, _pack = snapshot_with_pack
    cleared = clear_scratch_on_scene_end(snap, reason="scene_end", turn=0)
    assert cleared == 0


def test_clear_scratch_handles_multiple_chars(
    snapshot_with_pack,
    character_named_sam,
):
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)

    # Build a second character inline
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    second = Character(
        core=CreatureCore(
            name="Lia",
            description="Nimble",
            personality="bold",
            inventory=Inventory(),
        ),
        char_class="Scout",
        race="Human",
        backstory="...",
    )
    snap.characters.append(second)

    _add_status(snap.characters[0], "Choked", StatusSeverity.Scratch)
    _add_status(snap.characters[1], "Twisted wrist", StatusSeverity.Scratch)
    _add_status(snap.characters[1], "Captured", StatusSeverity.Wound)

    cleared = clear_scratch_on_scene_end(snap, reason="scene_end", turn=5)

    assert cleared == 2
    assert snap.characters[0].core.statuses == []
    assert [s.text for s in snap.characters[1].core.statuses] == ["Captured"]


# ---------------------------------------------------------------------------
# Unit tests — apply_explicit_status_clears
# ---------------------------------------------------------------------------


def test_explicit_clear_removes_named_status(
    snapshot_with_pack,
    character_named_sam,
):
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Captured by the Butcher's count", StatusSeverity.Wound)
    _add_status(sam, "Bruised", StatusSeverity.Scratch)

    cleared = apply_explicit_status_clears(
        snap,
        status_changes=[
            {"actor": "Sam", "clear": "Captured"},
        ],
        turn=4,
    )

    assert cleared == 1
    remaining = [s.text for s in sam.core.statuses]
    assert "Captured by the Butcher's count" not in remaining
    assert "Bruised" in remaining


def test_explicit_clear_case_insensitive_substring(
    snapshot_with_pack,
    character_named_sam,
):
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Choked — fingers at the throat", StatusSeverity.Scratch)

    cleared = apply_explicit_status_clears(
        snap,
        status_changes=[{"actor": "Sam", "clear": "choked"}],
        turn=1,
    )
    assert cleared == 1
    assert sam.core.statuses == []


def test_explicit_clear_unknown_actor_logs_warning(
    snapshot_with_pack,
    character_named_sam,
    caplog,
):
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    with caplog.at_level("WARNING"):
        cleared = apply_explicit_status_clears(
            snap,
            status_changes=[{"actor": "Ghost", "clear": "x"}],
            turn=0,
        )
    assert cleared == 0
    assert any("status_clear.unknown_actor" in r.message for r in caplog.records)


def test_explicit_clear_no_match_logs_warning(
    snapshot_with_pack,
    character_named_sam,
    caplog,
):
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Bruised Ribs", StatusSeverity.Wound)
    with caplog.at_level("WARNING"):
        cleared = apply_explicit_status_clears(
            snap,
            status_changes=[{"actor": "Sam", "clear": "Choked"}],
            turn=0,
        )
    assert cleared == 0
    # Status preserved
    assert any(s.text == "Bruised Ribs" for s in sam.core.statuses)
    assert any("status_clear.no_match" in r.message for r in caplog.records)


def test_explicit_clear_empty_or_missing_field_skipped(
    snapshot_with_pack,
    character_named_sam,
):
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    cleared = apply_explicit_status_clears(
        snap,
        status_changes=[
            {"actor": "Sam"},  # no clear key
            {"actor": "Sam", "clear": ""},  # empty clear
            {"clear": "Captured"},  # missing actor
        ],
        turn=0,
    )
    assert cleared == 0


# ---------------------------------------------------------------------------
# Wiring tests — proves narration_apply pipeline calls the helpers
# (CLAUDE.md "every test suite needs a wiring test")
# ---------------------------------------------------------------------------


def test_wiring_narration_apply_clears_scratch_on_location_change(
    snapshot_with_pack,
    character_named_sam,
):
    """Production path: narrator emits a new location → Scratch sweeps."""
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    snap.character_locations["Sam"] = "The Throat"
    _add_status(sam, "Choked", StatusSeverity.Scratch)
    _add_status(sam, "Bruised Ribs", StatusSeverity.Wound)

    result = NarrationTurnResult(
        narration="They march on.",
        location="The Antechamber",
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))

    remaining = [s.text for s in sam.core.statuses]
    assert "Choked" not in remaining, "Scratch should clear on scene change"
    assert "Bruised Ribs" in remaining, "Wound persists across scenes"


def test_wiring_narration_apply_first_location_does_not_sweep(
    snapshot_with_pack,
    character_named_sam,
):
    """Edge case: session start, snapshot.location is empty/falsy —
    sweeping on first set would wipe statuses created during chargen
    prose. The production guard is ``if old_loc and old_loc != new`` so
    None and "" both bypass the sweep."""
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    # snap.character_locations is empty at start — Sam has no per-character
    # entry until the first narration emits one.
    assert "Sam" not in snap.character_locations
    _add_status(sam, "Lingering doubt", StatusSeverity.Scratch)

    result = NarrationTurnResult(
        narration="They begin.",
        location="The Throat",
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))

    assert any(s.text == "Lingering doubt" for s in sam.core.statuses)


def test_wiring_narration_apply_handles_explicit_clear_entry(
    snapshot_with_pack,
    character_named_sam,
):
    """Production path: narrator emits {"actor":..,"clear":..} → status drops."""
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Captured by the Butcher", StatusSeverity.Wound)

    result = NarrationTurnResult(
        narration="She slips the rope.",
        status_changes=[{"actor": "Sam", "clear": "Captured"}],
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))

    assert all("Captured" not in s.text for s in sam.core.statuses)


def test_wiring_narration_apply_clear_and_add_in_same_turn(
    snapshot_with_pack,
    character_named_sam,
):
    """Mixed batch: clear an old status AND add a new one in one turn.
    Exercises the order-of-ops decision (clears run first; adds aren't
    swept by the just-applied clears).
    """
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Captured", StatusSeverity.Wound)

    result = NarrationTurnResult(
        narration="She breaks free but takes a cut.",
        status_changes=[
            {"actor": "Sam", "clear": "Captured"},
            {"actor": "Sam", "status": {"text": "Sliced palm", "severity": "Scratch"}},
        ],
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))

    texts = [s.text for s in sam.core.statuses]
    assert "Captured" not in texts
    assert "Sliced palm" in texts


def test_wiring_status_clear_module_imports():
    """Smoke test: the helper module is importable from production paths.
    A typo in the module path would silently leave clearing un-wired and
    the bug would re-surface — this catches that.
    """
    # narration_apply imports inside function bodies; force-resolve them.
    from sidequest.server.narration_apply import (  # noqa: F401
        _apply_narration_result_to_snapshot,
    )
    from sidequest.server.status_clear import (  # noqa: F401
        apply_explicit_status_clears,
        clear_scratch_on_scene_end,
    )


def test_wiring_session_handler_imports_status_clear():
    """The dice-resolved branch must route scene-end through Session.end_scene.

    Post Task E.3 (session-aggregate strangler): handlers/dice_throw.py
    no longer imports ``clear_scratch_on_scene_end`` directly. It calls
    ``sd._room.session.end_scene(...)`` which runs the scratch sweep
    (and advances the orbital clock) inside ``Session.end_scene``. The
    front-door wiring is what we're guarding against regression here —
    if this regresses, Bug #1 (Scratch never clears post-dice-resolve)
    re-surfaces along with the loss of the clock.advance span.
    """
    import importlib
    import inspect

    dh = importlib.import_module("sidequest.handlers.dice_throw")
    text = inspect.getsource(dh)
    assert "sd._room.session.end_scene" in text, (
        "dice_throw handler must route scene-end through Session.end_scene "
        "(which sweeps Scratch and advances the clock); otherwise Bug #1 "
        "regresses on dice-resolved encounters"
    )


def test_wiring_yield_action_imports_status_clear():
    """The YIELD path resolves an encounter too — must clear Scratch.

    Post Task E.1 (session-aggregate strangler): yield_action.py no
    longer calls ``clear_scratch_on_scene_end`` directly. It calls
    ``room.session.end_scene(...)`` which runs the scratch sweep (and
    advances the orbital clock) inside ``Session.end_scene``. The
    front-door wiring is what we're guarding against regression here.
    """
    import importlib
    import inspect

    ya = importlib.import_module("sidequest.server.dispatch.yield_action")
    text = inspect.getsource(ya)
    assert "room.session.end_scene" in text, (
        "yield_action.py must route scene-end through Session.end_scene "
        "(which sweeps Scratch and advances the clock); otherwise Bug #1 "
        "regresses on yields"
    )


# ---------------------------------------------------------------------------
# OTEL wiring — the GM panel lie-detector hook
# ---------------------------------------------------------------------------


def test_clear_emits_watcher_event(
    snapshot_with_pack,
    character_named_sam,
    monkeypatch,
):
    """clear_scratch_on_scene_end must publish state_transition events so
    the GM panel can verify the clear actually fired (CLAUDE.md OTEL
    Observability Principle).
    """
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Choked", StatusSeverity.Scratch)

    captured: list[tuple[str, dict, dict]] = []

    def _spy(evt_type, payload, **kwargs):
        captured.append((evt_type, dict(payload), kwargs))

    monkeypatch.setattr(
        "sidequest.server.status_clear._watcher_publish",
        _spy,
    )

    clear_scratch_on_scene_end(snap, reason="scene_end", turn=7)

    assert any(
        evt == "state_transition"
        and payload.get("op") == "status_cleared"
        and payload.get("actor") == "Sam"
        and payload.get("text") == "Choked"
        and payload.get("reason") == "scene_end"
        for evt, payload, _ in captured
    ), f"missing status_cleared event: {captured}"


def test_explicit_clear_emits_watcher_event(
    snapshot_with_pack,
    character_named_sam,
    monkeypatch,
):
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Captured", StatusSeverity.Wound)

    captured: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr(
        "sidequest.server.status_clear._watcher_publish",
        lambda et, p, **kw: captured.append((et, dict(p), kw)),
    )

    apply_explicit_status_clears(
        snap,
        status_changes=[{"actor": "Sam", "clear": "Captured"}],
        turn=2,
    )

    cleared_events = [
        p for et, p, _ in captured if et == "state_transition" and p.get("op") == "status_cleared"
    ]
    assert len(cleared_events) == 1
    assert cleared_events[0]["reason"] == "narrator_clear"
    assert cleared_events[0]["severity"] == "Wound"


# Sanity: severity Enum values match the OTEL span attributes the GM panel
# subscribes to. If someone renames the enum, the GM panel filter breaks
# silently — this fails loudly.
def test_severity_enum_values_match_otel_contract():
    assert StatusSeverity.Scratch.value == "Scratch"
    assert StatusSeverity.Wound.value == "Wound"
    assert StatusSeverity.Scar.value == "Scar"


# Lock down: no unintended sweep paths. If a future PR wires Wound-clearing
# into a "scene end" trigger by mistake, this guards the contract documented
# in game/status.py.
def test_scene_end_does_not_clear_wound_or_scar(
    snapshot_with_pack,
    character_named_sam,
):
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    sam = snap.characters[0]
    _add_status(sam, "Bruised Ribs", StatusSeverity.Wound)
    _add_status(sam, "Marked", StatusSeverity.Scar)

    clear_scratch_on_scene_end(snap, reason="scene_end", turn=0)

    texts = sorted(s.text for s in sam.core.statuses)
    assert texts == ["Bruised Ribs", "Marked"]


@pytest.mark.parametrize("reason", ["scene_end", "location_change"])
def test_clear_accepts_documented_reasons(
    snapshot_with_pack,
    character_named_sam,
    reason,
):
    """Belt-and-braces: both production reasons round-trip through the
    helper without errors."""
    snap, _pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    _add_status(snap.characters[0], "x", StatusSeverity.Scratch)
    cleared = clear_scratch_on_scene_end(snap, reason=reason, turn=0)
    assert cleared == 1
