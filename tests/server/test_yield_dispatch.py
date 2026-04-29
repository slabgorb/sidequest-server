import pytest

from sidequest.game.creature_core import RecoveryTrigger


def test_recovery_trigger_on_yield_constant():
    assert RecoveryTrigger.OnYield == "OnYield"


from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    StructuredEncounter,
)
from sidequest.game.status import Status, StatusSeverity
from sidequest.server.dispatch.yield_action import handle_yield


def _enc(*, p_metric=4, o_metric=7):
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=p_metric, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=o_metric, starting=0, threshold=10),
        actors=[
            EncounterActor(name="Sam", role="combatant", side="player"),
            EncounterActor(name="Promo", role="combatant", side="opponent"),
        ],
    )


def test_yield_solo_pc_resolves_encounter_immediately(snapshot_with_pack, character_named_sam):
    snap, _ = snapshot_with_pack
    snap.encounter = _enc()
    snap.characters.append(character_named_sam)
    handle_yield(snap, player_id="p1", player_name="Sam")
    assert snap.encounter.resolved is True
    assert snap.encounter.outcome == "yielded"
    assert snap.pending_resolution_signal.outcome == "yielded"
    assert snap.pending_resolution_signal.yielded_actors == ("Sam",)


def test_yield_refunds_edge_one_plus_status_count(snapshot_with_pack, character_named_sam):
    snap, _ = snapshot_with_pack
    snap.encounter = _enc()
    sam = character_named_sam
    sam.core.statuses.extend([
        Status(text="Bruised Ribs", severity=StatusSeverity.Wound,
               absorbed_shifts=0, created_turn=2, created_in_encounter="combat"),
        Status(text="Mocked", severity=StatusSeverity.Scratch,
               absorbed_shifts=0, created_turn=3, created_in_encounter="combat"),
    ])
    sam.core.edge.current = 0
    sam.core.edge.max = 5
    snap.characters.append(sam)
    handle_yield(snap, player_id="p1", player_name="Sam")
    # Both statuses created in this encounter → refund 1 + 2 = 3
    assert sam.core.edge.current == 3
    assert snap.pending_resolution_signal.edge_refreshed == 3


def test_yield_does_not_count_pre_existing_statuses(snapshot_with_pack, character_named_sam):
    snap, _ = snapshot_with_pack
    snap.encounter = _enc()
    sam = character_named_sam
    sam.core.statuses.append(Status(
        text="Old Scar", severity=StatusSeverity.Scar, absorbed_shifts=0,
        created_turn=0, created_in_encounter=None,
    ))
    sam.core.edge.current = 0
    sam.core.edge.max = 5
    snap.characters.append(sam)
    handle_yield(snap, player_id="p1", player_name="Sam")
    # Pre-existing status not in this encounter → refund 1 + 0 = 1
    assert sam.core.edge.current == 1


def test_yield_caps_at_edge_max(snapshot_with_pack, character_named_sam):
    snap, _ = snapshot_with_pack
    snap.encounter = _enc()
    sam = character_named_sam
    sam.core.edge.current = 4
    sam.core.edge.max = 5
    snap.characters.append(sam)
    handle_yield(snap, player_id="p1", player_name="Sam")
    assert sam.core.edge.current == 5  # capped at max


def test_yield_with_no_active_encounter_raises(snapshot_with_pack, character_named_sam):
    snap, _ = snapshot_with_pack
    snap.encounter = None
    snap.characters.append(character_named_sam)
    with pytest.raises(ValueError, match="no active encounter"):
        handle_yield(snap, player_id="p1", player_name="Sam")


def test_yield_with_two_pcs_first_yield_keeps_encounter_active(snapshot_with_pack):
    snap, _ = snapshot_with_pack
    enc = _enc()
    enc.actors.append(EncounterActor(name="Alex", role="combatant", side="player"))
    snap.encounter = enc
    # Each PC needs a Character entry
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, placeholder_edge_pool
    snap.characters.append(Character(
        core=CreatureCore(name="Sam", description="x", personality="x",
                          edge=placeholder_edge_pool()),
        backstory="x", char_class="Rogue", race="Human",
    ))
    snap.characters.append(Character(
        core=CreatureCore(name="Alex", description="x", personality="x",
                          edge=placeholder_edge_pool()),
        backstory="x", char_class="Warrior", race="Elf",
    ))
    handle_yield(snap, player_id="p1", player_name="Sam")
    # Sam withdrawn; Alex still active → encounter not resolved
    assert snap.encounter.resolved is False
    assert next(a for a in snap.encounter.actors if a.name == "Sam").withdrawn is True
    assert next(a for a in snap.encounter.actors if a.name == "Alex").withdrawn is False

    # Alex yields too → resolves
    handle_yield(snap, player_id="p2", player_name="Alex")
    assert snap.encounter.resolved is True
    assert snap.encounter.outcome == "yielded"
    assert set(snap.pending_resolution_signal.yielded_actors) == {"Sam", "Alex"}


def test_yield_emits_watcher_events_with_resolved_last(snapshot_with_pack, character_named_sam):
    """Watcher events fire in row order: yield_received → yield_resolved → resolved.
    The kinds[-1] == ENCOUNTER_RESOLVED invariant must hold for solo yield."""
    from sidequest.game.persistence import SqliteStore
    from sidequest.telemetry.watcher_hub import bind_event_store

    snap, _ = snapshot_with_pack
    snap.encounter = _enc()
    snap.characters.append(character_named_sam)

    store = SqliteStore.open_in_memory()
    bind_event_store(store)
    try:
        handle_yield(snap, player_id="p1", player_name="Sam")
        rows = list(store._conn.execute(
            "SELECT kind FROM events WHERE kind LIKE 'ENCOUNTER_%' ORDER BY seq"
        ).fetchall())
        kinds = [r[0] for r in rows]
        assert "ENCOUNTER_YIELD" in kinds, f"missing ENCOUNTER_YIELD; got {kinds}"
        assert kinds[-1] == "ENCOUNTER_RESOLVED", f"last row must be ENCOUNTER_RESOLVED; got {kinds}"
    finally:
        bind_event_store(None)
        store.close()
