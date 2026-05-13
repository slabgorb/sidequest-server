"""Tests for status_changes wiring in _apply_narration_result_to_snapshot.

Task 19 — Wire status_changes into engine state mutation.
"""

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.session import Npc
from sidequest.game.status import StatusSeverity
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from tests._helpers.session_room import room_for


def test_status_change_appends_to_named_actor(snapshot_with_pack, character_named_sam):
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    result = NarrationTurnResult(
        narration="Sam grunts.",
        status_changes=[
            {"actor": "Sam", "status": {"text": "Bruised Ribs", "severity": "Wound"}},
        ],
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))
    sam = snap.characters[0]
    assert any(
        s.text == "Bruised Ribs" and s.severity is StatusSeverity.Wound for s in sam.core.statuses
    )


def test_unknown_actor_in_status_change_is_dropped_with_warning(
    snapshot_with_pack,
    character_named_sam,
    caplog,
):
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    result = NarrationTurnResult(
        narration="...",
        status_changes=[{"actor": "Ghost", "status": {"text": "x", "severity": "Scratch"}}],
    )
    with caplog.at_level("WARNING"):
        _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))
    assert any("status_change.unknown_actor" in r.message for r in caplog.records)


def test_invalid_severity_in_status_change_is_dropped_with_warning(
    snapshot_with_pack,
    character_named_sam,
    caplog,
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
        _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))
    assert any("status_change.invalid_severity" in r.message for r in caplog.records)
    # And no status was appended
    assert all(s.text != "BadStatus" for s in snap.characters[0].core.statuses)


def test_empty_actor_or_text_in_status_change_is_silently_dropped(
    snapshot_with_pack,
    character_named_sam,
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
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))
    assert snap.characters[0].core.statuses == []


# ---------------------------------------------------------------------------
# Boon severity — added 2026-04-30 for prose-described temporary buffs
# from workings/consumables/scrolls/potions/artifacts. Mira's pouch-of-
# potion-glass playtest surfaced the gap: narrator wrote a real magical
# effect ("the torchlight gets clearer") but had no schema slot for the
# buff, so the system never recorded it. Boon is the slot.
# ---------------------------------------------------------------------------


def test_boon_severity_appends_to_named_actor(snapshot_with_pack, character_named_sam):
    """Boon is a valid severity tier and lands in the actor's status list."""
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    result = NarrationTurnResult(
        narration="Sam drinks; the torchlight gets clearer.",
        status_changes=[
            {
                "actor": "Sam",
                "status": {
                    "text": "Heightened Perception (3 rounds)",
                    "severity": "Boon",
                },
            },
        ],
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))
    sam = snap.characters[0]
    assert any(
        s.text == "Heightened Perception (3 rounds)" and s.severity is StatusSeverity.Boon
        for s in sam.core.statuses
    )


# ---------------------------------------------------------------------------
# Playtest 2026-05-09 BUG: status_change targeting an NPC (either fully
# instantiated or only auto-registered into npc_pool) was rejected with
# `status_change.unknown_actor` because the resolver only searched
# `snapshot.characters`. The dying delver in Sünden was a pool-only member,
# so triage statuses (stab wound, broken leg) fell on the floor.
# ---------------------------------------------------------------------------


def _make_npc(name: str, *, role: str = "townsfolk") -> Npc:
    from sidequest.game.creature_core import CreatureCore, Inventory

    return Npc(
        core=CreatureCore(
            name=name,
            description=f"A {role}",
            personality=role,
            inventory=Inventory(),
        ),
        pronouns="they/them",
    )


def test_status_change_appends_to_named_npc(snapshot_with_pack, character_named_sam):
    """Status changes targeting a fully-instantiated NPC land on its core.statuses."""
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    snap.npcs.append(_make_npc("Brecca", role="recruiter"))
    result = NarrationTurnResult(
        narration="Brecca winces, hand at her ribs.",
        status_changes=[
            {"actor": "Brecca", "status": {"text": "Bruised Ribs", "severity": "Wound"}},
        ],
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))
    brecca = next(n for n in snap.npcs if n.core.name == "Brecca")
    assert any(
        s.text == "Bruised Ribs" and s.severity is StatusSeverity.Wound
        for s in brecca.core.statuses
    )


def test_status_change_promotes_pool_member_to_npc_and_applies(
    snapshot_with_pack, character_named_sam, caplog
):
    """A status_change targeting an auto-registered pool-only NPC promotes
    them to a full Npc (per Wave 2A docs) and applies the status. No
    unknown_actor warning fires.
    """
    from sidequest.game.npc_pool import NpcPoolMember

    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    # Mirror the playtest scenario: narrator-invented unnamed body, auto-
    # registered into the pool but never promoted.
    snap.npc_pool.append(
        NpcPoolMember(
            name="Wounded Sünden delver",
            role="wounded stranger",
            pronouns="they/them",
            appearance="wool gambeson, rough leathers, empty scabbard",
            drawn_from="narrator_invented",
        )
    )
    result = NarrationTurnResult(
        narration="Willes binds the wound; the delver's leg is bent below the knee.",
        status_changes=[
            {
                "actor": "Wounded Sünden delver",
                "status": {
                    "text": "Stab wound, lower back left — gambeson saturated",
                    "severity": "Wound",
                },
            },
            {
                "actor": "Wounded Sünden delver",
                "status": {"text": "Broken left leg below the knee", "severity": "Wound"},
            },
        ],
    )
    with caplog.at_level("WARNING"):
        _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))

    # Promotion happened: a full Npc now exists with the same name.
    promoted = [n for n in snap.npcs if n.core.name == "Wounded Sünden delver"]
    assert len(promoted) == 1, (
        f"Expected pool member to be promoted to a single Npc; got {len(promoted)}"
    )
    npc = promoted[0]
    # Provenance preserved per Sebastien lie-detector contract.
    assert npc.pool_origin == "Wounded Sünden delver"
    # Both statuses landed on the promoted NPC.
    texts = {s.text for s in npc.core.statuses}
    assert "Stab wound, lower back left — gambeson saturated" in texts
    assert "Broken left leg below the knee" in texts
    # No unknown_actor warning — the bug is closed.
    assert not any("status_change.unknown_actor" in r.message for r in caplog.records), (
        "Pool-resident NPC should not produce unknown_actor warning after promotion"
    )


def test_status_change_truly_unknown_actor_still_warns(
    snapshot_with_pack, character_named_sam, caplog
):
    """A status_change actor not in characters, npcs, OR npc_pool still
    logs unknown_actor — the warning hasn't been silenced, only narrowed.
    """
    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    result = NarrationTurnResult(
        narration="...",
        status_changes=[{"actor": "PhantomNobody", "status": {"text": "x", "severity": "Scratch"}}],
    )
    with caplog.at_level("WARNING"):
        _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))
    assert any(
        "status_change.unknown_actor" in r.message and "PhantomNobody" in r.message
        for r in caplog.records
    )


def test_status_clear_finds_named_npc(snapshot_with_pack, character_named_sam):
    """Explicit status clears resolve against snapshot.npcs, not just
    snapshot.characters. Symmetric with the add path.
    """
    from sidequest.game.status import Status

    snap, pack = snapshot_with_pack
    snap.characters.append(character_named_sam)
    npc = _make_npc("Brecca", role="recruiter")
    npc.core.statuses.append(
        Status(text="Bruised Ribs", severity=StatusSeverity.Wound, absorbed_shifts=0)
    )
    snap.npcs.append(npc)
    result = NarrationTurnResult(
        narration="Brecca rolls her shoulders; the bruise fades.",
        status_changes=[{"actor": "Brecca", "clear": "Bruised Ribs"}],
    )
    _apply_narration_result_to_snapshot(snap, result, "Sam", pack=pack, room=room_for(snap))
    brecca = next(n for n in snap.npcs if n.core.name == "Brecca")
    assert all(s.text != "Bruised Ribs" for s in brecca.core.statuses)
