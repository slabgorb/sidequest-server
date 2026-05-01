"""Cross-file validators run after world load."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sidequest.genre.loader import (
    GenreLoadError,
    _validate_crew_npc_references,
    _validate_opening_setting_references,
)
from sidequest.genre.models.authored_npc import AuthoredNpc
from sidequest.genre.models.narrative import (
    Opening,
    OpeningSetting,
    OpeningTrigger,
)
from sidequest.genre.models.rigs_world import (
    ChassisInstanceConfig,
    OceanScores,
)


def _make_chassis(crew_npcs: list[str] | None = None) -> ChassisInstanceConfig:
    return ChassisInstanceConfig(
        id="kestrel",
        name="Kestrel",
        **{"class": "voidborn_freighter"},
        OCEAN=OceanScores(),
        interior_rooms=["galley", "cockpit", "engineering"],
        crew_npcs=crew_npcs or [],
    )


def _make_opening(
    setting: OpeningSetting,
    mode: str = "solo",
    backgrounds: list[str] | None = None,
) -> Opening:
    return Opening(
        id="test_op",
        triggers=OpeningTrigger(mode=mode, backgrounds=backgrounds or []),
        setting=setting,
        establishing_narration="Galley scene; coffee is what passes for coffee.",
        first_turn_invitation="Outside the porthole, void and stars.",
    )


def test_chassis_anchored_resolves() -> None:
    op = _make_opening(
        OpeningSetting(chassis_instance="kestrel", interior_room="galley")
    )
    chassis = [_make_chassis()]
    # Should not raise.
    _validate_opening_setting_references([op], chassis, world_slug="testworld")


def test_chassis_instance_unknown_fails() -> None:
    """Validator 2."""
    op = _make_opening(
        OpeningSetting(chassis_instance="missing_ship", interior_room="galley")
    )
    chassis = [_make_chassis()]
    with pytest.raises(GenreLoadError, match="chassis_instance"):
        _validate_opening_setting_references([op], chassis, world_slug="testworld")


def test_interior_room_not_in_chassis_fails() -> None:
    """Validator 3."""
    op = _make_opening(
        OpeningSetting(chassis_instance="kestrel", interior_room="bridge")  # not in interior_rooms
    )
    chassis = [_make_chassis()]
    with pytest.raises(GenreLoadError, match="interior_room"):
        _validate_opening_setting_references([op], chassis, world_slug="testworld")


def test_location_anchored_skips_chassis_check() -> None:
    """Location-anchored openings don't need chassis_instance to resolve."""
    op = _make_opening(OpeningSetting(location_label="the Promenade"))
    chassis: list[ChassisInstanceConfig] = []
    # Should not raise — no chassis required for location-anchored.
    _validate_opening_setting_references([op], chassis, world_slug="testworld")


def _make_authored_npc(id: str) -> AuthoredNpc:
    return AuthoredNpc(id=id, name=f"Name-{id}")


def test_crew_npcs_all_resolve() -> None:
    chassis = [_make_chassis(crew_npcs=["captain_x", "engineer_y"])]
    npcs = [_make_authored_npc("captain_x"), _make_authored_npc("engineer_y")]
    _validate_crew_npc_references(chassis, npcs, world_slug="testworld")


def test_crew_npc_unknown_fails() -> None:
    """Validator 4."""
    chassis = [_make_chassis(crew_npcs=["captain_x", "missing_npc"])]
    npcs = [_make_authored_npc("captain_x")]
    with pytest.raises(GenreLoadError, match="missing_npc"):
        _validate_crew_npc_references(chassis, npcs, world_slug="testworld")


def test_empty_crew_npcs_ok() -> None:
    """A chassis with no crew_npcs declared is valid."""
    chassis = [_make_chassis(crew_npcs=[])]
    npcs: list[AuthoredNpc] = []
    _validate_crew_npc_references(chassis, npcs, world_slug="testworld")
