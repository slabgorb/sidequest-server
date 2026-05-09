"""Distribution tests for the calibrated opposed_check resolver (ADR-093).

The calibration v1 changes three numeric levers:

1. Opponent stats 12 → 10 (modifier +1 → +0)
2. Tie band ±2 → ±1
3. Confrontation thresholds 10 → 7 for combat/chase

These tests cover (1) and (2) — the dice-side calibration. They verify that
under the calibrated parameters (player +1 vs opponent +0, tie band ±1) the
opposed_check resolver produces the analytical expected tier distribution.

ADR-093 quoted approximate percentages (45/33/22) for this distribution.
The exact analytical distribution, computed by enumerating all 400 d20×d20
pairs, is:

    CritSuccess:  16.50%   ┐
    Success:      31.00%   ├─ Success-or-better: 47.50%
    Tie:          14.25%
    Fail:         27.00%   ┐
    CritFail:     11.25%   ├─ Fail-or-worse:    38.25%

The ADR's Tie ≈ 33% and Fail ≈ 22% predictions overstated the Tie band's
share (the narrower ±1 band covers only 3 of 39 weighted shifts, not the
~33% the ADR estimated) and understated the Fail-or-worse rate. The
qualitative claim — that the player gains a real edge (Success+ rises from
~38% under parity to ~48% under calibration) and that ties drop sharply
(~24% → ~14%) — holds. See deviations log on session file 45-41 for the
amendment request to ADR-093.

These tests assert the analytical distribution within ±5pp tolerance.
"""

from __future__ import annotations

import random

import pytest

from sidequest.game.encounter import EncounterActor
from sidequest.game.opposed_check import resolve_opposed_check
from sidequest.genre.models.rules import BeatDef, ConfrontationDef
from sidequest.protocol.dice import RollOutcome

# ---------------------------------------------------------------------------
# Test fixtures: minimal cdef + beat for the resolver
# ---------------------------------------------------------------------------


def _calibrated_cdef() -> ConfrontationDef:
    """A combat ConfrontationDef matching the calibrated v1 parameters:
    threshold=7, opposed_check resolution mode."""
    return ConfrontationDef.model_validate(
        {
            "type": "combat",
            "label": "Calibrated Combat",
            "category": "combat",
            "resolution_mode": "opposed_check",
            "opponent_default_stats": {"STR": 10},
            "player_metric": {"name": "momentum", "starting": 0, "threshold": 7},
            "opponent_metric": {"name": "momentum", "starting": 0, "threshold": 7},
            "beats": [
                {
                    "id": "attack",
                    "label": "Attack",
                    "kind": "strike",
                    "base": 2,
                    "stat_check": "STR",
                },
            ],
        }
    )


def _attack_beat() -> BeatDef:
    return BeatDef.model_validate(
        {
            "id": "attack",
            "label": "Attack",
            "kind": "strike",
            "base": 2,
            "stat_check": "STR",
        }
    )


# ---------------------------------------------------------------------------
# Analytical expected distribution (enumeration of all 400 d20×d20 pairs)
# ---------------------------------------------------------------------------

# Player stat 12 → modifier +1, opponent stat 10 → modifier +0.
# These constants are the calibrated v1 targets per ADR-093.
PLAYER_SCORE = 12
OPPONENT_SCORE = 10

# Analytical distribution under calibrated parameters (computed by
# enumerating d1, d2 in {1..20}). Tolerances are wide enough that
# Monte-Carlo noise on n=10_000 rolls won't flake (binomial stddev for
# the rarest band is < 0.4pp, so ±5pp is comfortable).
EXPECTED_SUCCESS_OR_BETTER_PCT = 47.5
EXPECTED_TIE_PCT = 14.25
EXPECTED_FAIL_OR_WORSE_PCT = 38.25
TOLERANCE_PCT = 5.0

# Number of Monte-Carlo trials. 10k is the AC's stated sample size — large
# enough to keep Monte-Carlo noise an order of magnitude below the ±5pp
# tolerance.
N_TRIALS = 10_000

# Deterministic seed so the test is repeatable. 45041 = story 45-41.
SEED = 45041


# ---------------------------------------------------------------------------
# Monte-Carlo distribution test (load-bearing AC)
# ---------------------------------------------------------------------------


def test_calibrated_distribution_meets_adr_093_targets():
    """10,000 simulated opposed_checks at calibrated parameters produce a
    tier distribution within ±5pp of the analytical expected values.

    Player score 12 (mod +1) vs opponent score 10 (mod +0), tie band ±1.

    This is the load-bearing AC for ADR-093 — it proves the calibration
    actually moves the distribution to the documented target. Failing this
    test means either (a) the tie band wasn't narrowed, or (b) the
    modifier delta isn't being applied — both of which would render the
    calibration ineffective.
    """
    rng = random.Random(SEED)
    player = EncounterActor(
        name="Sam",
        role="combatant",
        side="player",
        per_actor_state={"stats": {"STR": PLAYER_SCORE}},
    )
    opponent = EncounterActor(
        name="Wolf",
        role="combatant",
        side="opponent",
        per_actor_state={"stats": {"STR": OPPONENT_SCORE}},
    )
    cdef = _calibrated_cdef()
    beat = _attack_beat()

    # Wiring guard for both calibration levers. Without this assertion, the
    # ±5pp tolerance on Success-or-better cannot distinguish "narrow band +
    # calibrated stats" (47.50%) from "narrow band + parity stats at 12"
    # (42.75% — also within the ±5pp window). Pinning both modifiers makes
    # the distribution test refuse to pass when only one of the two levers
    # has fired.
    sentinel = resolve_opposed_check(
        player_actor=player,
        opponent_actor=opponent,
        player_beat=beat,
        opponent_beat=beat,
        cdef=cdef,
        player_roll=10,
        opponent_roll=10,
    )
    assert sentinel.player_mod == 1, (
        f"Player modifier must be +1 (score {PLAYER_SCORE}); got "
        f"{sentinel.player_mod}. Calibration assumes player point-buy "
        "average mod of +1."
    )
    assert sentinel.opponent_mod == 0, (
        f"Opponent modifier must be 0 (score {OPPONENT_SCORE}); got "
        f"{sentinel.opponent_mod}. If this fails, opponent_default_stats "
        "was not lowered from 12 — distribution assertions below will pass "
        "vacuously without this guard."
    )

    success_or_better = 0
    tie = 0
    fail_or_worse = 0

    for _ in range(N_TRIALS):
        p_roll = rng.randint(1, 20)
        o_roll = rng.randint(1, 20)
        result = resolve_opposed_check(
            player_actor=player,
            opponent_actor=opponent,
            player_beat=beat,
            opponent_beat=beat,
            cdef=cdef,
            player_roll=p_roll,
            opponent_roll=o_roll,
        )
        if result.tier in (RollOutcome.Success, RollOutcome.CritSuccess):
            success_or_better += 1
        elif result.tier is RollOutcome.Tie:
            tie += 1
        else:
            fail_or_worse += 1

    success_pct = success_or_better / N_TRIALS * 100
    tie_pct = tie / N_TRIALS * 100
    fail_pct = fail_or_worse / N_TRIALS * 100

    # Sanity: counts must sum to N_TRIALS — every roll classifies exactly once.
    assert success_or_better + tie + fail_or_worse == N_TRIALS

    assert abs(success_pct - EXPECTED_SUCCESS_OR_BETTER_PCT) <= TOLERANCE_PCT, (
        f"Success-or-better rate {success_pct:.2f}% outside "
        f"{EXPECTED_SUCCESS_OR_BETTER_PCT}% ± {TOLERANCE_PCT}pp — calibration "
        f"is not delivering the player's edge"
    )
    assert abs(tie_pct - EXPECTED_TIE_PCT) <= TOLERANCE_PCT, (
        f"Tie rate {tie_pct:.2f}% outside {EXPECTED_TIE_PCT}% ± "
        f"{TOLERANCE_PCT}pp — tie band may not have been narrowed to ±1"
    )
    assert abs(fail_pct - EXPECTED_FAIL_OR_WORSE_PCT) <= TOLERANCE_PCT, (
        f"Fail-or-worse rate {fail_pct:.2f}% outside "
        f"{EXPECTED_FAIL_OR_WORSE_PCT}% ± {TOLERANCE_PCT}pp"
    )


def test_calibrated_distribution_player_edge_is_real():
    """Sanity-check on the calibration intent: under calibrated parameters,
    player Success-or-better must exceed Fail-or-worse by a clear margin.

    Pre-calibration (parity, ±2 band) had Success+ ≈ Fail+ ≈ 38%. Post-
    calibration the narrower band and +1 player modifier shift Success+
    above Fail+ by roughly 9pp. If this assertion fails, the calibration
    is not producing the SOUL "player feels the build matters" effect even
    if the absolute percentages happen to fall within the tolerance band.
    """
    rng = random.Random(SEED + 1)
    player = EncounterActor(
        name="Sam",
        role="combatant",
        side="player",
        per_actor_state={"stats": {"STR": PLAYER_SCORE}},
    )
    opponent = EncounterActor(
        name="Wolf",
        role="combatant",
        side="opponent",
        per_actor_state={"stats": {"STR": OPPONENT_SCORE}},
    )
    cdef = _calibrated_cdef()
    beat = _attack_beat()

    success_or_better = 0
    fail_or_worse = 0
    for _ in range(N_TRIALS):
        result = resolve_opposed_check(
            player_actor=player,
            opponent_actor=opponent,
            player_beat=beat,
            opponent_beat=beat,
            cdef=cdef,
            player_roll=rng.randint(1, 20),
            opponent_roll=rng.randint(1, 20),
        )
        if result.tier in (RollOutcome.Success, RollOutcome.CritSuccess):
            success_or_better += 1
        elif result.tier in (RollOutcome.Fail, RollOutcome.CritFail):
            fail_or_worse += 1

    edge_pp = (success_or_better - fail_or_worse) / N_TRIALS * 100
    # Analytical edge is 47.5 - 38.25 = 9.25pp; demand at least 5pp margin
    # so the test catches regressions where the band shrinks but the
    # opponent modifier didn't drop.
    assert edge_pp >= 5.0, (
        f"Player edge {edge_pp:.2f}pp below 5pp floor — calibration is not "
        f"delivering the player advantage. Check that opponent stats are 10 "
        f"(mod +0) and player stats default to 12 (mod +1)."
    )


# ---------------------------------------------------------------------------
# Tie-band shape: explicit assertions on the calibrated band geometry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "p_score, o_score, p_roll, o_roll, expected_tier",
    [
        # Calibrated parity rolls — both 10s, player +1, opp +0 → shift +1 → Tie.
        (12, 10, 10, 10, RollOutcome.Tie),
        # Player rolls 1pt above opponent → shift +2 → Success (was Tie pre-calibration).
        (12, 10, 11, 10, RollOutcome.Success),
        # Player rolls 1pt below opponent → shift +0 → Tie.
        (12, 10, 10, 11, RollOutcome.Tie),
        # Player rolls 2pt below → shift -1 → Tie (still in band).
        (12, 10, 9, 11, RollOutcome.Tie),
        # Player rolls 3pt below → shift -2 → Fail (was Tie pre-calibration).
        (12, 10, 8, 11, RollOutcome.Fail),
        # Player crit on 20, opponent on 11 → shift +10 → CritSuccess (boundary unchanged).
        (12, 10, 20, 11, RollOutcome.CritSuccess),
        # Player rolls 1, opponent rolls 11 → shift -9 → Fail (one above CritFail boundary).
        (12, 10, 1, 11, RollOutcome.Fail),
        # Player rolls 1, opponent rolls 12 → shift -10 → CritFail (boundary unchanged).
        (12, 10, 1, 12, RollOutcome.CritFail),
    ],
)
def test_calibrated_band_geometry(
    p_score: int,
    o_score: int,
    p_roll: int,
    o_roll: int,
    expected_tier: RollOutcome,
):
    """Boundary geometry under calibrated parameters. Each row verifies a
    specific shift outcome; together they pin the calibrated bands so
    future drift away from ADR-093 surfaces as a test failure."""
    player = EncounterActor(
        name="Sam",
        role="combatant",
        side="player",
        per_actor_state={"stats": {"STR": p_score}},
    )
    opponent = EncounterActor(
        name="Wolf",
        role="combatant",
        side="opponent",
        per_actor_state={"stats": {"STR": o_score}},
    )
    cdef = _calibrated_cdef()
    beat = _attack_beat()
    result = resolve_opposed_check(
        player_actor=player,
        opponent_actor=opponent,
        player_beat=beat,
        opponent_beat=beat,
        cdef=cdef,
        player_roll=p_roll,
        opponent_roll=o_roll,
    )
    assert result.tier is expected_tier, (
        f"Calibrated band miss: p_score={p_score}, o_score={o_score}, "
        f"p_roll={p_roll}, o_roll={o_roll}, shift={result.shift}, "
        f"got {result.tier}, expected {expected_tier}"
    )
