"""Tests for StationSpec and ChassisClass.stations."""

import pytest
from pydantic import ValidationError

from sidequest.genre.models.chassis import ChassisClass, StationSpec


def test_station_spec_required_fields():
    s = StationSpec(
        id="helm",
        display_name="Helm",
        room="cockpit",
        preferred_role="pilot",
    )
    assert s.id == "helm"
    assert s.display_name == "Helm"
    assert s.room == "cockpit"
    assert s.preferred_role == "pilot"


def test_station_spec_preferred_role_optional():
    s = StationSpec(id="helm", display_name="Helm", room="cockpit")
    assert s.preferred_role is None


def test_station_spec_rejects_extra():
    with pytest.raises(ValidationError):
        StationSpec(
            id="helm",
            display_name="Helm",
            room="cockpit",
            bogus="x",
        )


def test_chassis_class_stations_default_empty():
    """Stations is optional; chassis classes that don't author stations get []."""
    c = ChassisClass(
        id="x",
        display_name="X",
        **{"class": "freighter"},
        provenance="voidborn_built",
        scale_band="vehicular",
        crew_model="flexible_roles",
    )
    assert c.stations == []


def test_chassis_class_with_stations():
    c = ChassisClass(
        id="x",
        display_name="X",
        **{"class": "freighter"},
        provenance="voidborn_built",
        scale_band="vehicular",
        crew_model="flexible_roles",
        stations=[
            StationSpec(id="helm", display_name="Helm", room="cockpit"),
            StationSpec(id="weapons", display_name="Weapons", room="cockpit"),
        ],
    )
    assert len(c.stations) == 2
    assert c.stations[0].id == "helm"
