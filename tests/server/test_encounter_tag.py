import pytest
from pydantic import ValidationError

from sidequest.game.encounter_tag import EncounterTag


def _kw(**over):
    base = dict(
        text="Off-Balance",
        created_by="Sam Jones",
        target="The Promo",
        leverage=1,
        fleeting=False,
        created_turn=3,
    )
    base.update(over)
    return base


def test_encounter_tag_full_round_trip():
    tag = EncounterTag(**_kw())
    raw = tag.model_dump_json()
    parsed = EncounterTag.model_validate_json(raw)
    assert parsed == tag


def test_encounter_tag_scene_target_is_none():
    tag = EncounterTag(**_kw(target=None))
    assert tag.target is None


def test_encounter_tag_fleeting_default_one_charge():
    tag = EncounterTag(**_kw(fleeting=True, leverage=1))
    assert tag.fleeting is True
    assert tag.leverage == 1


def test_encounter_tag_rejects_negative_leverage():
    with pytest.raises(ValidationError):
        EncounterTag(**_kw(leverage=-1))


def test_encounter_tag_extra_field_forbidden():
    with pytest.raises(ValidationError):
        EncounterTag(**_kw(), foo="bar")  # type: ignore[call-arg]
