import pytest
from pydantic import ValidationError

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    StructuredEncounter,
)
from sidequest.game.encounter_tag import EncounterTag


def _metric(*, current: int = 0, threshold: int = 10) -> EncounterMetric:
    return EncounterMetric(name="momentum", current=current, starting=0, threshold=threshold)


def _actor(side: str = "player", *, name: str = "Sam") -> EncounterActor:
    return EncounterActor(name=name, role="combatant", side=side)


def test_encounter_metric_is_ascending_only_no_direction_field():
    m = _metric()
    assert m.current == 0
    assert m.threshold == 10
    with pytest.raises(ValidationError):
        EncounterMetric(name="x", current=0, starting=0, threshold=10, direction="ascending")  # type: ignore[call-arg]


def test_encounter_actor_side_required_and_closed_enum():
    EncounterActor(name="Sam", role="combatant", side="player")
    EncounterActor(name="Promo", role="combatant", side="opponent")
    EncounterActor(name="Host", role="bystander", side="neutral")
    with pytest.raises(ValidationError):
        EncounterActor(name="???", role="x", side="enemy")  # type: ignore[arg-type]


def test_encounter_actor_withdrawn_default_false():
    a = _actor()
    assert a.withdrawn is False


def test_structured_encounter_dual_dials_and_tags():
    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=_metric(),
        opponent_metric=_metric(),
        actors=[_actor("player"), _actor("opponent", name="Promo")],
    )
    assert enc.player_metric.threshold == 10
    assert enc.opponent_metric.threshold == 10
    assert enc.tags == []
    assert enc.outcome is None
    assert enc.resolved is False


def test_structured_encounter_rejects_old_metric_field():
    with pytest.raises(ValidationError):
        StructuredEncounter(  # type: ignore[call-arg]
            encounter_type="combat",
            metric=_metric(),
            actors=[_actor()],
        )


def test_structured_encounter_round_trip_with_tags():
    tag = EncounterTag(
        text="Off-Balance",
        created_by="Sam",
        target="Promo",
        leverage=1,
        fleeting=False,
        created_turn=2,
    )
    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=_metric(current=4),
        opponent_metric=_metric(current=7),
        actors=[_actor("player"), _actor("opponent", name="Promo")],
        tags=[tag],
        outcome=None,
    )
    raw = enc.model_dump_json()
    parsed = StructuredEncounter.model_validate_json(raw)
    assert parsed.tags == [tag]
    assert parsed.player_metric.current == 4
    assert parsed.opponent_metric.current == 7


def test_structured_outcome_values():
    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=_metric(),
        opponent_metric=_metric(),
        actors=[_actor()],
        outcome="player_victory",
        resolved=True,
    )
    assert enc.outcome == "player_victory"


def test_metric_direction_enum_no_longer_importable():
    with pytest.raises(ImportError):
        from sidequest.game.encounter import MetricDirection  # noqa: F401
