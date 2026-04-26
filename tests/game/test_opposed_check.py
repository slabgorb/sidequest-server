"""Unit tests for opposed_check resolver — shift bands, stat sourcing, errors.

Spec: ``.archive/handoffs/opposed-checks-design.md`` (Architect, 2026-04-26).
The resolver is the heart of combat fairness — every combat encounter now
runs through it. These tests lock the contract:

- Shift band thresholds (boundary cases at +9/+10, +2/+3, -2/-3, -9/-10)
- Stat sourcing fallback chain (per_actor → cdef → hard fail)
- Resolver returns the correct tier for each band
- Hard-fail-loud when neither source carries the stat (CLAUDE.md rule)
"""

from __future__ import annotations

import pytest

from sidequest.game.encounter import EncounterActor
from sidequest.game.opposed_check import (
    OpposedRollResult,
    _ability_modifier,
    _tier_from_shift,
    resolve_opponent_modifier,
    resolve_opposed_check,
)
from sidequest.genre.models.rules import BeatDef, ConfrontationDef, ResolutionMode
from sidequest.protocol.dice import RollOutcome

# ---------------------------------------------------------------------------
# Shift band boundaries (spec table — locked thresholds)
# ---------------------------------------------------------------------------

def test_shift_at_plus_10_is_crit_success():
    assert _tier_from_shift(10) is RollOutcome.CritSuccess


def test_shift_at_plus_9_is_success_not_crit():
    """One below the CritSuccess threshold."""
    assert _tier_from_shift(9) is RollOutcome.Success


def test_shift_at_plus_3_is_success():
    assert _tier_from_shift(3) is RollOutcome.Success


def test_shift_at_plus_2_is_tie_not_success():
    """Top of the Tie band."""
    assert _tier_from_shift(2) is RollOutcome.Tie


def test_shift_at_zero_is_tie():
    assert _tier_from_shift(0) is RollOutcome.Tie


def test_shift_at_minus_2_is_tie_not_fail():
    """Bottom of the Tie band."""
    assert _tier_from_shift(-2) is RollOutcome.Tie


def test_shift_at_minus_3_is_fail():
    assert _tier_from_shift(-3) is RollOutcome.Fail


def test_shift_at_minus_9_is_fail_not_crit_fail():
    """One above the CritFail threshold."""
    assert _tier_from_shift(-9) is RollOutcome.Fail


def test_shift_at_minus_10_is_crit_fail():
    assert _tier_from_shift(-10) is RollOutcome.CritFail


def test_shift_at_minus_25_is_still_crit_fail():
    """Extreme shifts still classify; no clamp / overflow weirdness."""
    assert _tier_from_shift(-25) is RollOutcome.CritFail


def test_shift_at_plus_25_is_still_crit_success():
    assert _tier_from_shift(25) is RollOutcome.CritSuccess


# ---------------------------------------------------------------------------
# Ability modifier formula matches dice dispatcher (D&D-style floor)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "score,expected_mod",
    [
        (10, 0),
        (11, 0),
        (12, 1),
        (13, 1),
        (14, 2),
        (15, 2),
        (16, 3),
        (8, -1),
        (7, -2),
    ],
)
def test_ability_modifier_matches_dnd_table(score: int, expected_mod: int):
    assert _ability_modifier(score) == expected_mod


# ---------------------------------------------------------------------------
# Stat-sourcing helper — per_actor → cdef → hard fail
# ---------------------------------------------------------------------------

def _make_cdef(opponent_default_stats: dict[str, int] | None = None) -> ConfrontationDef:
    """Minimal valid ConfrontationDef for testing the resolver paths."""
    return ConfrontationDef.model_validate(
        {
            "type": "combat",
            "label": "Test Combat",
            "category": "combat",
            "resolution_mode": "opposed_check",
            "opponent_default_stats": opponent_default_stats,
            "player_metric": {"name": "momentum", "starting": 0, "threshold": 10},
            "opponent_metric": {"name": "momentum", "starting": 0, "threshold": 10},
            "beats": [
                {"id": "attack", "label": "Attack", "kind": "strike", "base": 2, "stat_check": "STR"},
            ],
        }
    )


def test_modifier_sourced_from_per_actor_state_first():
    actor = EncounterActor(
        name="Wolf", role="combatant", side="opponent",
        per_actor_state={"stats": {"STR": 16}},
    )
    cdef = _make_cdef(opponent_default_stats={"STR": 12})
    # per_actor wins over cdef
    assert resolve_opponent_modifier(actor=actor, cdef=cdef, stat_check="STR") == 3


def test_modifier_falls_back_to_cdef_when_per_actor_lacks_stat():
    actor = EncounterActor(
        name="Wolf", role="combatant", side="opponent",
        per_actor_state={"stats": {"DEX": 14}},  # no STR
    )
    cdef = _make_cdef(opponent_default_stats={"STR": 12})
    assert resolve_opponent_modifier(actor=actor, cdef=cdef, stat_check="STR") == 1


def test_modifier_lookup_is_case_insensitive_in_per_actor():
    actor = EncounterActor(
        name="Wolf", role="combatant", side="opponent",
        per_actor_state={"stats": {"str": 14}},  # lowercase
    )
    cdef = _make_cdef(opponent_default_stats=None)
    assert resolve_opponent_modifier(actor=actor, cdef=cdef, stat_check="STR") == 2


def test_modifier_lookup_is_case_insensitive_in_cdef():
    actor = EncounterActor(
        name="Wolf", role="combatant", side="opponent",
        per_actor_state={"stats": {"DEX": 12}},
    )
    cdef = _make_cdef(opponent_default_stats={"strength": 18})
    assert resolve_opponent_modifier(actor=actor, cdef=cdef, stat_check="Strength") == 4


def test_modifier_hard_fails_when_neither_source_has_stat():
    """CLAUDE.md no-silent-fallback: must raise, not silently return 0."""
    actor = EncounterActor(name="Wolf", role="combatant", side="opponent")
    cdef = _make_cdef(opponent_default_stats={"DEX": 12})
    with pytest.raises(ValueError, match="no stat 'STR'"):
        resolve_opponent_modifier(actor=actor, cdef=cdef, stat_check="STR")


def test_modifier_hard_fails_when_cdef_has_no_default_stats_at_all():
    """The most common authoring miss: pack adds opposed_check but forgets
    opponent_default_stats. Must explode loudly with the missing-stat
    name AND show that no fallback existed."""
    actor = EncounterActor(name="Wolf", role="combatant", side="opponent")
    cdef = _make_cdef(opponent_default_stats=None)
    with pytest.raises(ValueError, match="no stat 'STR'"):
        resolve_opponent_modifier(actor=actor, cdef=cdef, stat_check="STR")


def test_modifier_hard_fails_on_empty_stat_check():
    actor = EncounterActor(name="Wolf", role="combatant", side="opponent")
    cdef = _make_cdef(opponent_default_stats={"STR": 12})
    with pytest.raises(ValueError, match="non-empty stat_check"):
        resolve_opponent_modifier(actor=actor, cdef=cdef, stat_check="")


# ---------------------------------------------------------------------------
# resolve_opposed_check — full happy paths and tier returns
# ---------------------------------------------------------------------------

def _attack_beat(stat: str = "STR") -> BeatDef:
    return BeatDef.model_validate({
        "id": "attack", "label": "Attack", "kind": "strike", "base": 2,
        "stat_check": stat,
    })


def _make_resolver_actors(player_stats: dict[str, int], opponent_stats: dict[str, int]):
    player = EncounterActor(
        name="Sam", role="combatant", side="player",
        per_actor_state={"stats": player_stats},
    )
    opponent = EncounterActor(
        name="Wolf", role="combatant", side="opponent",
        per_actor_state={"stats": opponent_stats},
    )
    return player, opponent


@pytest.mark.parametrize(
    "p_roll,p_score,o_roll,o_score,expected_shift,expected_tier",
    [
        # Tied modifiers, identical rolls → shift 0 → Tie
        (10, 12, 10, 12, 0, RollOutcome.Tie),
        # Player rolls 18 (mod +1), opponent 8 (mod +1) → shift +10 → CritSuccess
        (18, 12, 8, 12, 10, RollOutcome.CritSuccess),
        # Player 14 (mod +2), opponent 9 (mod 0) → shift +7 → Success
        (14, 14, 9, 10, 7, RollOutcome.Success),
        # Player 6 (mod +0), opponent 12 (mod +1) → shift -7 → Fail
        (6, 10, 12, 12, -7, RollOutcome.Fail),
        # Player 3 (mod -2), opponent 18 (mod +3) → shift -20 → CritFail
        (3, 7, 18, 16, -20, RollOutcome.CritFail),
    ],
)
def test_resolve_opposed_check_happy_paths(
    p_roll: int, p_score: int, o_roll: int, o_score: int,
    expected_shift: int, expected_tier: RollOutcome,
):
    player, opponent = _make_resolver_actors(
        player_stats={"STR": p_score},
        opponent_stats={"STR": o_score},
    )
    cdef = _make_cdef()
    result = resolve_opposed_check(
        player_actor=player,
        opponent_actor=opponent,
        player_beat=_attack_beat(),
        opponent_beat=_attack_beat(),
        cdef=cdef,
        player_roll=p_roll,
        opponent_roll=o_roll,
    )
    assert isinstance(result, OpposedRollResult)
    assert result.shift == expected_shift, (
        f"shift mismatch: {result.shift} != {expected_shift}"
    )
    assert result.tier is expected_tier
    # Sanity: modifiers actually computed.
    assert result.player_mod == _ability_modifier(p_score)
    assert result.opponent_mod == _ability_modifier(o_score)


def test_resolve_opposed_check_uses_cdef_fallback_for_opponent():
    """Wiring: opponent has no per_actor stats; cdef.opponent_default_stats
    carries the value. No silent zero default."""
    player = EncounterActor(
        name="Sam", role="combatant", side="player",
        per_actor_state={"stats": {"STR": 14}},
    )
    opponent = EncounterActor(name="Wolf", role="combatant", side="opponent")
    cdef = _make_cdef(opponent_default_stats={"STR": 12})
    result = resolve_opposed_check(
        player_actor=player,
        opponent_actor=opponent,
        player_beat=_attack_beat(),
        opponent_beat=_attack_beat(),
        cdef=cdef,
        player_roll=10,
        opponent_roll=10,
    )
    # Player +2, opponent +1, both rolled 10 → shift = +1 → Tie.
    assert result.shift == 1
    assert result.tier is RollOutcome.Tie


def test_resolve_opposed_check_rejects_out_of_range_player_roll():
    player, opponent = _make_resolver_actors({"STR": 12}, {"STR": 12})
    with pytest.raises(ValueError, match="player_roll 21 not in 1..20"):
        resolve_opposed_check(
            player_actor=player, opponent_actor=opponent,
            player_beat=_attack_beat(), opponent_beat=_attack_beat(),
            cdef=_make_cdef(), player_roll=21, opponent_roll=10,
        )


def test_resolve_opposed_check_rejects_out_of_range_opponent_roll():
    player, opponent = _make_resolver_actors({"STR": 12}, {"STR": 12})
    with pytest.raises(ValueError, match="opponent_roll 0 not in 1..20"):
        resolve_opposed_check(
            player_actor=player, opponent_actor=opponent,
            player_beat=_attack_beat(), opponent_beat=_attack_beat(),
            cdef=_make_cdef(), player_roll=10, opponent_roll=0,
        )


def test_resolve_opposed_check_propagates_missing_stat_for_opponent():
    """Hard-fail-loud propagates from the resolver — caller sees the
    opponent's missing-stat error directly. No silent zero modifier."""
    player = EncounterActor(
        name="Sam", role="combatant", side="player",
        per_actor_state={"stats": {"STR": 14}},
    )
    opponent = EncounterActor(name="Wolf", role="combatant", side="opponent")
    cdef = _make_cdef(opponent_default_stats=None)  # no fallback
    with pytest.raises(ValueError, match="no stat 'STR'"):
        resolve_opposed_check(
            player_actor=player, opponent_actor=opponent,
            player_beat=_attack_beat(), opponent_beat=_attack_beat(),
            cdef=cdef, player_roll=10, opponent_roll=10,
        )


# ---------------------------------------------------------------------------
# Schema-side wiring: ConfrontationDef accepts opponent_default_stats and
# the new ResolutionMode.opposed_check variant
# ---------------------------------------------------------------------------

def test_confrontation_def_accepts_opposed_check_with_default_stats():
    cdef = _make_cdef(opponent_default_stats={"STR": 12, "DEX": 11})
    assert cdef.resolution_mode is ResolutionMode.opposed_check
    assert cdef.opponent_default_stats == {"STR": 12, "DEX": 11}


def test_confrontation_def_opposed_check_without_default_stats_loads_but_will_fail_at_resolve():
    """The schema does NOT enforce opponent_default_stats at load time —
    a per-actor stat block can satisfy the resolver. Hard-fail moves to
    runtime so packs that stuff stats on actors via narrative_card or
    fixtures still work without a global fallback."""
    cdef = _make_cdef(opponent_default_stats=None)
    assert cdef.resolution_mode is ResolutionMode.opposed_check
    assert cdef.opponent_default_stats is None
