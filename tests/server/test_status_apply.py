"""Tests for status_changes wiring in _apply_narration_result_to_snapshot.

Task 19 — Wire status_changes into engine state mutation.
"""
from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.status import StatusSeverity
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot


def test_status_change_appends_to_named_actor(snapshot_with_pack, character_named_sam):
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    result = NarrationTurnResult(
        narration="Sam grunts.",
        status_changes=[
            {"actor": "Sam", "status": {"text": "Bruised Ribs", "severity": "Wound"}},
        ],
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack)
    sam = snap.characters[0]
    assert any(
        s.text == "Bruised Ribs" and s.severity is StatusSeverity.Wound
        for s in sam.core.statuses
    )


def test_unknown_actor_in_status_change_is_dropped_with_warning(
    snapshot_with_pack, character_named_sam, caplog,
):
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    result = NarrationTurnResult(
        narration="...",
        status_changes=[{"actor": "Ghost", "status": {"text": "x", "severity": "Scratch"}}],
    )
    with caplog.at_level("WARNING"):
        _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack)
    assert any("status_change.unknown_actor" in r.message for r in caplog.records)


def test_invalid_severity_in_status_change_is_dropped_with_warning(
    snapshot_with_pack, character_named_sam, caplog,
):
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    result = NarrationTurnResult(
        narration="...",
        status_changes=[
            {"actor": "Sam", "status": {"text": "BadStatus", "severity": "InvalidLevel"}},
        ],
    )
    with caplog.at_level("WARNING"):
        _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack)
    assert any("status_change.invalid_severity" in r.message for r in caplog.records)
    # And no status was appended
    assert all(s.text != "BadStatus" for s in snap.characters[0].core.statuses)


def test_empty_actor_or_text_in_status_change_is_silently_dropped(
    snapshot_with_pack, character_named_sam,
):
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    result = NarrationTurnResult(
        narration="...",
        status_changes=[
            {"actor": "", "status": {"text": "Status1", "severity": "Scratch"}},
            {"actor": "Sam", "status": {"text": "", "severity": "Scratch"}},
        ],
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack)
    assert snap.characters[0].core.statuses == []
