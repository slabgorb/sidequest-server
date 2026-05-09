from sidequest.genre.models.character import IdentityCapture, MechanicalEffects


def test_assignment_required_field_accepted():
    eff = MechanicalEffects(assignment_required=True)
    assert eff.assignment_required is True


def test_allow_reject_field_accepted():
    eff = MechanicalEffects(allow_reject=True)
    assert eff.allow_reject is True


def test_background_autogen_source_field_accepted():
    eff = MechanicalEffects(background_autogen_source="backstory_tables")
    assert eff.background_autogen_source == "backstory_tables"


def test_identity_capture_subscript():
    eff = MechanicalEffects(
        identity_capture=IdentityCapture(
            pronouns_required=True,
            background_optional=True,
            description_optional=True,
        )
    )
    assert eff.identity_capture.pronouns_required is True
    assert eff.identity_capture.background_optional is True


def test_unknown_field_still_forbidden():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MechanicalEffects(some_garbage_field=True)
