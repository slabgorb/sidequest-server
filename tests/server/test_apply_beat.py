from pathlib import Path

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
        player_metric=EncounterMetric(
            name="momentum", current=p_cur, starting=0, threshold=p_thresh
        ),
        opponent_metric=EncounterMetric(
            name="momentum", current=o_cur, starting=0, threshold=o_thresh
        ),
        actors=[
            EncounterActor(name="Sam", role="combatant", side="player"),
            EncounterActor(name="Promo", role="combatant", side="opponent"),
            EncounterActor(name="Host", role="bystander", side="neutral"),
        ],
    )


def _strike_beat(beat_id: str = "attack", base: int = 2) -> BeatDef:
    return BeatDef.model_validate(
        {
            "id": beat_id,
            "label": beat_id,
            "kind": "strike",
            "base": base,
            "stat_check": "STR",
        }
    )


def _angle_beat(beat_id: str = "feint", target_tag: str = "Off-Balance") -> BeatDef:
    return BeatDef.model_validate(
        {
            "id": beat_id,
            "label": beat_id,
            "kind": "angle",
            "target_tag": target_tag,
            "stat_check": "DEX",
        }
    )


def _push_beat(beat_id: str = "flee") -> BeatDef:
    return BeatDef.model_validate(
        {
            "id": beat_id,
            "label": beat_id,
            "kind": "push",
            "base": 1,
            "stat_check": "DEX",
        }
    )


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


def test_strike_fail_publishes_beat_no_op_for_gm_panel(monkeypatch):
    """Regression: per spec, default delta tables for Fail tier on every
    kind are {own=0, opponent=0} — a Fail rolls narratively but neither
    dial moves. Without an OTEL surface for this case, the GM panel
    sees the beat fire and assumes the engine is responsive; nothing
    surfaces the silent stalemate. Playtest 2026-04-25 [P0] flagged
    the dual-track engine as "decorative" specifically because of this
    invisibility — the engine works as specified, but Fails feel inert.
    The `state_transition op=beat_no_op` event makes the design choice
    observable so Sebastien can see the encounter didn't progress.
    """
    captured: list[tuple[str, dict, dict]] = []

    def fake_publish(event_type, fields, *, component="", severity="info"):
        captured.append((event_type, fields, {"component": component, "severity": severity}))

    import sidequest.game.beat_kinds as _bk

    monkeypatch.setattr(_bk, "_watcher_publish", fake_publish)

    enc = _enc()
    sam = enc.find_actor("Sam")
    apply_beat(enc, sam, _strike_beat(base=2), RollOutcome.Fail)

    no_ops = [
        (fields, meta)
        for et, fields, meta in captured
        if et == "state_transition" and fields.get("op") == "beat_no_op"
    ]
    assert no_ops, "Fail-tier strike must publish beat_no_op for GM panel visibility"
    fields, meta = no_ops[0]
    assert fields["actor_side"] == "player"
    assert fields["beat_kind"] == "strike"
    assert "spec" in fields["rationale"].lower()
    assert meta["component"] == "encounter"


def test_strike_success_does_not_publish_beat_no_op(monkeypatch):
    """Counterpart: a Success-tier strike DOES move the dial, so the
    no-op event must NOT fire (otherwise the GM panel would mis-flag a
    working engine as inert)."""
    captured: list[str] = []

    def fake_publish(event_type, fields, *, component="", severity="info"):
        if event_type == "state_transition" and fields.get("op") == "beat_no_op":
            captured.append(fields["beat_id"])

    import sidequest.game.beat_kinds as _bk

    monkeypatch.setattr(_bk, "_watcher_publish", fake_publish)

    enc = _enc()
    sam = enc.find_actor("Sam")
    apply_beat(enc, sam, _strike_beat(base=2), RollOutcome.Success)
    assert captured == []


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
    bash = BeatDef.model_validate(
        {
            "id": "shield_bash",
            "label": "Shield Bash",
            "kind": "strike",
            "base": 4,
            "stat_check": "STR",
            "deltas": {"crit_fail": {"own": -2}},
        }
    )
    apply_beat(enc, sam, bash, RollOutcome.CritFail)
    # CritFail on strike default is 0 own; override drops to -2; ascending dial
    # is clamped at 0 (from spec — never go negative on a side's own dial)
    assert enc.player_metric.current == 0


# ──────────────────────────────────────────────────────────────────────────
# Content-tuning regression: space_opera/negotiation deltas must give
# opponent leverage on Fail/CritFail (playtest 2026-05-03 [BUG] — Edge meters
# never advance from failed rolls).
#
# Two test layers:
#  - Engine layer: per-tier ``opponent: +N`` override on a strike beat
#    advances the opponent dial when the player rolls Fail/CritFail.
#    No filesystem dep — pins the apply_beat seam.
#  - Content layer: load the real space_opera pack from sidequest-content/
#    and verify the negotiation beats carry the override. Bypasses the
#    test conftest's fixture symlink (which redirects ``space_opera`` to
#    ``test_genre``, a stripped mutant_wasteland clone with no negotiation
#    confrontation). Skips when the content dir is absent so the suite
#    stays portable on a sidequest-server-only checkout.
# ──────────────────────────────────────────────────────────────────────────


def test_strike_fail_with_opponent_override_advances_opponent_metric():
    """Engine seam — verify per-tier ``opponent: +N`` override on strike Fail.

    DEFAULT_DELTAS gives strike ``Fail: 0/0``; a beat that sets
    ``deltas.fail.opponent = 1`` must override that and credit the opposing
    side's metric when the actor rolls Fail.
    """
    enc = _enc()
    sam = enc.find_actor("Sam")
    persuade = BeatDef.model_validate(
        {
            "id": "persuade",
            "label": "Make Your Case",
            "kind": "strike",
            "base": 2,
            "stat_check": "Influence",
            "deltas": {"fail": {"opponent": 1}, "crit_fail": {"opponent": 2}},
        }
    )
    apply_beat(enc, sam, persuade, RollOutcome.Fail)
    assert enc.opponent_metric.current == 1
    assert enc.player_metric.current == 0  # player's own dial unchanged on Fail


def test_strike_critfail_opponent_override_outpaces_fail():
    """CritFail must hit harder than Fail when both have opponent overrides."""
    persuade = BeatDef.model_validate(
        {
            "id": "persuade",
            "label": "Make Your Case",
            "kind": "strike",
            "base": 2,
            "stat_check": "Influence",
            "deltas": {"fail": {"opponent": 1}, "crit_fail": {"opponent": 2}},
        }
    )
    enc_fail = _enc()
    apply_beat(enc_fail, enc_fail.find_actor("Sam"), persuade, RollOutcome.Fail)
    enc_crit = _enc()
    apply_beat(enc_crit, enc_crit.find_actor("Sam"), persuade, RollOutcome.CritFail)
    assert enc_crit.opponent_metric.current > enc_fail.opponent_metric.current


def test_angle_fail_with_opponent_override_advances_opponent_metric():
    """Same seam test for angle (concede_point is a negotiation angle beat)."""
    enc = _enc()
    sam = enc.find_actor("Sam")
    concede = BeatDef.model_validate(
        {
            "id": "concede_point",
            "label": "Concede a Point",
            "kind": "angle",
            "target_tag": "Real Goal Revealed",
            "stat_check": "Influence",
            "deltas": {"fail": {"opponent": 1}, "crit_fail": {"opponent": 2}},
        }
    )
    apply_beat(enc, sam, concede, RollOutcome.Fail)
    assert enc.opponent_metric.current == 1


def test_push_fail_with_opponent_override_advances_opponent_metric():
    """Same seam test for push (walk_away is a negotiation push beat)."""
    enc = _enc()
    sam = enc.find_actor("Sam")
    walk_away = BeatDef.model_validate(
        {
            "id": "walk_away",
            "label": "Walk Away",
            "kind": "push",
            "stat_check": "Resolve",
            "deltas": {"fail": {"opponent": 1}, "crit_fail": {"opponent": 2}},
        }
    )
    apply_beat(enc, sam, walk_away, RollOutcome.Fail)
    assert enc.opponent_metric.current == 1


def test_space_opera_negotiation_beats_carry_opponent_overrides():
    """Content layer — the real space_opera pack must declare the overrides.

    Loads via ``load_genre_pack`` (path-explicit, cache-free) to bypass the
    test conftest's fixture symlink that redirects ``space_opera`` → frozen
    ``test_genre`` (a stripped mutant_wasteland clone with no negotiation
    confrontation). Skips when the content dir is absent.
    """
    import pytest

    from sidequest.genre.loader import load_genre_pack

    content = Path("/Users/slabgorb/Projects/oq-2/sidequest-content/genre_packs/space_opera")
    if not content.is_dir():
        pytest.skip("sidequest-content not on disk in this checkout")

    pack = load_genre_pack(content)
    negotiation = next(
        c for c in pack.rules.confrontations if c.confrontation_type == "negotiation"
    )
    expected_ids = {"persuade", "threaten", "concede_point", "walk_away"}
    assert {b.id for b in negotiation.beats} == expected_ids, (
        "space_opera negotiation beat roster drift — playtest fix expects "
        "all four beats to carry per-tier opponent overrides"
    )
    for beat in negotiation.beats:
        assert beat.deltas is not None, (
            f"negotiation beat {beat.id!r} dropped its deltas override — "
            f"DEFAULT_DELTAS Fail tier is 0/0 so the dial would freeze"
        )
        assert "fail" in beat.deltas and "opponent" in beat.deltas["fail"], (
            f"negotiation beat {beat.id!r} missing fail.opponent override"
        )
        assert beat.deltas["fail"]["opponent"] >= 1
        assert "crit_fail" in beat.deltas
        assert beat.deltas["crit_fail"]["opponent"] >= beat.deltas["fail"]["opponent"]
