import pytest

from sidequest.game.status import Status, StatusSeverity, migrate_legacy_statuses


def test_status_severity_enum_values():
    assert StatusSeverity.Scratch.value == "Scratch"
    assert StatusSeverity.Wound.value == "Wound"
    assert StatusSeverity.Scar.value == "Scar"


def test_status_full_construction():
    s = Status(
        text="Cracked Temple",
        severity=StatusSeverity.Wound,
        absorbed_shifts=0,
        created_turn=4,
        created_in_encounter="combat",
    )
    assert s.text == "Cracked Temple"
    assert s.severity is StatusSeverity.Wound
    assert s.absorbed_shifts == 0
    assert s.created_in_encounter == "combat"


def test_status_round_trip_json():
    s = Status(
        text="Bleeding",
        severity=StatusSeverity.Scratch,
        absorbed_shifts=0,
        created_turn=0,
        created_in_encounter=None,
    )
    raw = s.model_dump_json()
    parsed = Status.model_validate_json(raw)
    assert parsed == s


def test_migrate_bare_string_list_to_status_list():
    legacy = ["Bleeding", "Stunned"]
    migrated = migrate_legacy_statuses(legacy)
    assert len(migrated) == 2
    assert all(isinstance(s, Status) for s in migrated)
    assert migrated[0].text == "Bleeding"
    assert migrated[0].severity is StatusSeverity.Scratch
    assert migrated[0].absorbed_shifts == 0
    assert migrated[0].created_turn == 0
    assert migrated[0].created_in_encounter is None


def test_migrate_already_structured_statuses_passes_through():
    existing = [Status(
        text="Wound",
        severity=StatusSeverity.Wound,
        absorbed_shifts=2,
        created_turn=5,
        created_in_encounter="combat",
    )]
    migrated = migrate_legacy_statuses(existing)
    assert migrated == existing


def test_migrate_mixed_list_raises():
    # Mixing dict and bare string is a content bug — fail loud.
    with pytest.raises(TypeError):
        migrate_legacy_statuses(["Bleeding", 12345])  # type: ignore[list-item]
