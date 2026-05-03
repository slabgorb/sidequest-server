"""Cross-validation for crew stations on a chassis class.

Per CLAUDE.md No Silent Fallbacks: an unknown room reference raises
with explicit context — station id, bad room, and the list of valid
rooms — so the failure is diagnosable from one log line.
"""

from sidequest.genre.models.chassis import ChassisClass


class InteriorLoaderError(ValueError):
    """Raised when a chassis class has invalid station data."""


def validate_chassis_stations(chassis: ChassisClass) -> None:
    """Raise InteriorLoaderError if any station references an unknown room."""
    valid_rooms = {r.id for r in chassis.interior_rooms}
    for station in chassis.stations:
        if station.room not in valid_rooms:
            raise InteriorLoaderError(
                f"Station {station.id!r} references unknown room "
                f"{station.room!r}; valid rooms on chassis "
                f"{chassis.id!r}: {sorted(valid_rooms)}"
            )
