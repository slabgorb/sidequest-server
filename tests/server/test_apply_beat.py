from sidequest.game.beat_kinds import (
    apply_beat,
)
from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    StructuredEncounter,
)
from sidequest.genre.models.rules import BeatDef
from sidequest.protocol.dice import RollOutcome


def _enc(*, p_thresh: int = 10, o_thresh: int = 10, p_cur: int = 0, o_cur: int = 0):
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=p_cur, starting=0, threshold=p_thresh),
        opponent_metric=EncounterMetric(name="momentum", current=o_cur, starting=0, threshold=o_thresh),
        actors=[
            EncounterActor(name="Sam", role="combatant", side="player"),
            EncounterActor(name="Promo", role="combatant", side="opponent"),
            EncounterActor(name="Host", role="bystander", side="neutral"),
        ],
    )


def _strike_beat(beat_id: str = "attack", base: int = 2) -> BeatDef:
    return BeatDef.model_validate({
        "id": beat_id, "label": beat_id, "kind": "strike", "base": base,
        "stat_check": "STR",
    })


def _angle_beat(beat_id: str = "feint", target_tag: str = "Off-Balance") -> BeatDef:
    return BeatDef.model_validate({
        "id": beat_id, "label": beat_id, "kind": "angle",
        "target_tag": target_tag, "stat_check": "DEX",
    })


def _push_beat(beat_id: str = "flee") -> BeatDef:
    return BeatDef.model_validate({
        "id": beat_id, "label": beat_id, "kind": "push", "base": 1,
        "stat_check": "DEX",
    })


def test_strike_player_advances_player_metric_only():
    enc = _enc()
    sam = enc.find_actor("Sam")
    result = apply_beat(enc, sam, _strike_beat(base=2), RollOutcome.Success)
    assert enc.player_metric.current == 2
    assert enc.opponent_metric.current == 0
    assert result.resolved is False


def test_strike_opponent_advances_opponent_metric_only():
    enc = _enc()
    promo = enc.find_actor("Promo")
    result = apply_beat(enc, promo, _strike_beat(base=3), RollOutcome.Success)
    assert enc.player_metric.current == 0
    assert enc.opponent_metric.current == 3
    assert result.resolved is False


def test_neutral_actor_skipped_no_dial_change():
    enc = _enc()
    host = enc.find_actor("Host")
    result = apply_beat(enc, host, _strike_beat(), RollOutcome.Success)
    assert enc.player_metric.current == 0
    assert enc.opponent_metric.current == 0
    assert result.skipped_reason == "neutral_actor"


def test_withdrawn_actor_skipped():
    enc = _enc()
    sam = enc.find_actor("Sam")
    sam.withdrawn = True
    result = apply_beat(enc, sam, _strike_beat(), RollOutcome.Success)
    assert enc.player_metric.current == 0
    assert result.skipped_reason == "withdrawn_actor"


def test_threshold_cross_player_first_yields_player_victory():
    enc = _enc(p_cur=8)
    sam = enc.find_actor("Sam")
    apply_beat(enc, sam, _strike_beat(base=3), RollOutcome.Success)
    assert enc.player_metric.current == 11
    assert enc.resolved is True
    assert enc.outcome == "player_victory"


def test_threshold_cross_opponent_yields_opponent_victory():
    enc = _enc(o_cur=8)
    promo = enc.find_actor("Promo")
    apply_beat(enc, promo, _strike_beat(base=3), RollOutcome.Success)
    assert enc.opponent_metric.current == 11
    assert enc.resolved is True
    assert enc.outcome == "opponent_victory"


def test_push_success_resolves_with_resolution_beat_outcome():
    enc = _enc()
    sam = enc.find_actor("Sam")
    apply_beat(enc, sam, _push_beat("flee"), RollOutcome.Success)
    assert enc.resolved is True
    assert enc.outcome == "resolution_beat:flee"


def test_angle_success_creates_persistent_tag_with_leverage_one():
    enc = _enc()
    sam = enc.find_actor("Sam")
    apply_beat(enc, sam, _angle_beat("feint", "Off-Balance"), RollOutcome.Success)
    assert len(enc.tags) == 1
    tag = enc.tags[0]
    assert tag.text == "Off-Balance"
    assert tag.leverage == 1
    assert tag.fleeting is False
    assert tag.created_by == "Sam"


def test_angle_critsuccess_creates_tag_with_leverage_two():
    enc = _enc()
    sam = enc.find_actor("Sam")
    apply_beat(enc, sam, _angle_beat("feint", "Off-Balance"), RollOutcome.CritSuccess)
    assert enc.tags[0].leverage == 2


def test_angle_critfail_backfires_tag_onto_opposite_side():
    enc = _enc()
    sam = enc.find_actor("Sam")
    apply_beat(enc, sam, _angle_beat("feint", "Off-Balance"), RollOutcome.CritFail)
    assert len(enc.tags) == 1
    tag = enc.tags[0]
    assert tag.fleeting is True
    assert tag.target == "Promo"


def test_strike_critsuccess_creates_fleeting_opening_tag():
    enc = _enc()
    sam = enc.find_actor("Sam")
    apply_beat(enc, sam, _strike_beat(base=2), RollOutcome.CritSuccess)
    assert any(t.text == "Opening" and t.fleeting for t in enc.tags)


def test_post_resolution_apply_is_dropped_with_skipped_reason():
    enc = _enc()
    enc.resolved = True
    enc.outcome = "player_victory"
    sam = enc.find_actor("Sam")
    result = apply_beat(enc, sam, _strike_beat(), RollOutcome.Success)
    assert result.skipped_reason == "encounter_resolved"
    assert enc.player_metric.current == 0


def test_per_tier_override_applies_critfail_own_minus_two():
    enc = _enc()
    sam = enc.find_actor("Sam")
    bash = BeatDef.model_validate({
        "id": "shield_bash", "label": "Shield Bash", "kind": "strike", "base": 4,
        "stat_check": "STR",
        "deltas": {"crit_fail": {"own": -2}},
    })
    apply_beat(enc, sam, bash, RollOutcome.CritFail)
    # CritFail on strike default is 0 own; override drops to -2; ascending dial
    # is clamped at 0 (from spec — never go negative on a side's own dial)
    assert enc.player_metric.current == 0
