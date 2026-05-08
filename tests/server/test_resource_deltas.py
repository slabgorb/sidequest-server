"""Tests for beat.resource_deltas consumption via _apply_narration_result_to_snapshot.

Story §5.6 #4: cast_spell must decrement spell_slots ledger bar after each cast.

Three tests:
1. cast_spell (delta -1.0) drops spell_slots from 1.0 → 0.0.
2. cast_spell with partial slots (0.5) is clamped to 0.0, not -0.5.
3. A non-delta beat (no resource_deltas) leaves the ledger unchanged (sanity).
"""

from __future__ import annotations

from sidequest.agents.orchestrator import BeatSelection, NarrationTurnResult
from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    StructuredEncounter,
)
from sidequest.game.session import GameSnapshot
from sidequest.genre.models.rules import BeatDef, ConfrontationDef, RulesConfig
from sidequest.magic.models import HardLimit, LedgerBarSpec, WorldKnowledge, WorldMagicConfig
from sidequest.magic.state import BarKey, MagicState
from sidequest.protocol.dice import RollOutcome
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from tests._helpers.session_room import room_for

# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------


def _spell_slots_bar_spec() -> LedgerBarSpec:
    """Minimal spell_slots bar spec — mirrors caverns_sunden/magic.yaml."""
    return LedgerBarSpec(
        id="spell_slots",
        scope="character",
        direction="down",
        range=(0.0, 1.0),
        threshold_low=0.0,
        consequence_on_low_cross="no spells until rest",
        starts_at_chargen={"Mage": 1.0},
    )


def _minimal_world_config() -> WorldMagicConfig:
    """Minimal WorldMagicConfig for a Mage-bearing world."""
    return WorldMagicConfig(
        world_slug="test_dungeon",
        genre_slug="caverns_and_claudes",
        allowed_sources=["arcane"],
        active_plugins=[],
        intensity=0.5,
        world_knowledge=WorldKnowledge(primary="folkloric"),
        visibility={"primary": "feared"},
        hard_limits=[HardLimit(id="test_limit", description="x")],
        cost_types=["spell_slots"],
        ledger_bars=[_spell_slots_bar_spec()],
        narrator_register="test",
    )


def _magic_state_with_mage(character_name: str, *, slots: float) -> MagicState:
    """Build a MagicState with spell_slots bar set to ``slots`` for ``character_name``."""
    config = _minimal_world_config()
    state = MagicState.from_config(config)
    # add_character seeds the bar at starts_at_chargen["Mage"] = 1.0;
    # then we explicitly set it to the requested value.
    state.add_character(character_name, character_class="Mage")
    key = BarKey(scope="character", owner_id=character_name, bar_id="spell_slots")
    state.set_bar_value(key, slots)
    return state


def _cast_spell_beat() -> BeatDef:
    """cast_spell beat with resource_deltas: {spell_slots: -1.0}."""
    return BeatDef.model_validate(
        {
            "id": "cast_spell",
            "label": "Cast Spell",
            "kind": "strike",
            "base": 4,
            "stat_check": "INT",
            "resource_deltas": {"spell_slots": -1.0},
        }
    )


def _attack_beat() -> BeatDef:
    """Plain attack beat — no resource_deltas."""
    return BeatDef.model_validate(
        {
            "id": "attack",
            "label": "Attack",
            "kind": "strike",
            "base": 2,
            "stat_check": "STR",
        }
    )


def _pack_with_combat(beat: BeatDef):
    """Build a minimal GenrePack with a combat confrontation containing ``beat``."""
    from sidequest.genre.models.pack import GenrePack

    cdef = ConfrontationDef.model_validate(
        {
            "type": "combat",
            "label": "Combat",
            "category": "combat",
            "player_metric": {"name": "momentum", "threshold": 10},
            "opponent_metric": {"name": "momentum", "threshold": 10},
            "beats": [
                beat.model_dump(mode="python"),
                # Need at least one other beat on the opponent side so the
                # encounter has an opponent-actor beat; we add a plain attack.
                {
                    "id": "goblin_attack",
                    "label": "Goblin Attack",
                    "kind": "strike",
                    "base": 1,
                    "stat_check": "STR",
                },
            ],
        }
    )
    rules = RulesConfig(confrontations=[cdef])
    # GenrePack has many optional fields; model_construct bypasses validation
    # for fields we don't need in this test context.
    return GenrePack.model_construct(
        meta=None,
        rules=rules,
        lore=None,
        theme=None,
        visual_style=None,
        progression=None,
        axes=None,
        audio=None,
        prompts=None,
    )


def _combat_encounter(character_name: str) -> StructuredEncounter:
    """Minimal combat encounter with ``character_name`` as the player actor."""
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        actors=[
            EncounterActor(name=character_name, role="combatant", side="player"),
            EncounterActor(name="Goblin", role="combatant", side="opponent"),
        ],
    )


def _snap_with_encounter(character_name: str, *, slots: float) -> tuple[GameSnapshot, object]:
    """Build a (snapshot, pack) pair for resource_deltas tests.

    The snapshot has:
    - an active combat encounter with character as player actor
    - magic_state with spell_slots bar at ``slots``
    """
    beat = _cast_spell_beat()
    pack = _pack_with_combat(beat)
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.encounter = _combat_encounter(character_name)
    snap.magic_state = _magic_state_with_mage(character_name, slots=slots)
    return snap, pack


def _get_slots(snap: GameSnapshot, character_name: str) -> float:
    """Read spell_slots bar value from snap.magic_state."""
    key = BarKey(scope="character", owner_id=character_name, bar_id="spell_slots")
    bar = snap.magic_state.get_bar(key)
    return bar.value


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cast_spell_decrements_spell_slot_ledger():
    """Mage casts cast_spell once; spell_slots bar drops 1.0 → 0.0."""
    character_name = "Aldric"
    snap, pack = _snap_with_encounter(character_name, slots=1.0)

    # Confirm pre-condition.
    assert _get_slots(snap, character_name) == 1.0

    result = NarrationTurnResult(
        narration="Aldric weaves a bolt of arcane fire.",
        beat_selections=[
            BeatSelection(
                actor=character_name,
                beat_id="cast_spell",
                outcome=RollOutcome.Success,
            )
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name=character_name,
        pack=pack,
        room=room_for(snap),
        from_explicit_action=True,
    )

    assert _get_slots(snap, character_name) == 0.0, (
        "spell_slots must drop from 1.0 to 0.0 after a cast_spell beat"
    )


def test_cast_spell_clamps_at_zero_not_below():
    """cast_spell at slots=0.5 cannot go negative — clamped to 0.0."""
    character_name = "Mirela"
    snap, pack = _snap_with_encounter(character_name, slots=0.5)

    assert _get_slots(snap, character_name) == 0.5

    result = NarrationTurnResult(
        narration="Mirela pushes through her dwindling reserves.",
        beat_selections=[
            BeatSelection(
                actor=character_name,
                beat_id="cast_spell",
                outcome=RollOutcome.Success,
            )
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name=character_name,
        pack=pack,
        room=room_for(snap),
        from_explicit_action=True,
    )

    # Delta is -1.0 from 0.5 = -0.5 before clamp. Must be 0.0.
    assert _get_slots(snap, character_name) == 0.0, "spell_slots must clamp at 0.0, not go to -0.5"


def test_beat_without_resource_deltas_leaves_ledger_unchanged():
    """Sanity: a plain attack beat does not touch any ledger bar."""
    character_name = "Ragnar"
    # Build a pack with the plain attack beat (no resource_deltas).
    plain_beat = _attack_beat()
    pack = _pack_with_combat(plain_beat)
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.encounter = _combat_encounter(character_name)
    snap.magic_state = _magic_state_with_mage(character_name, slots=1.0)

    assert _get_slots(snap, character_name) == 1.0

    result = NarrationTurnResult(
        narration="Ragnar swings his sword at the goblin.",
        beat_selections=[
            BeatSelection(
                actor=character_name,
                beat_id="attack",
                outcome=RollOutcome.Success,
            )
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name=character_name,
        pack=pack,
        room=room_for(snap),
        from_explicit_action=True,
    )

    # spell_slots should be untouched.
    assert _get_slots(snap, character_name) == 1.0, (
        "non-resource-delta beats must not modify the ledger"
    )
