"""Tests for the crew_npcs extension on ChassisInstanceConfig."""

from __future__ import annotations

from sidequest.genre.models.rigs_world import ChassisInstanceConfig


def test_chassis_instance_default_no_crew() -> None:
    cfg = ChassisInstanceConfig(
        id="kestrel",
        name="Kestrel",
        **{"class": "voidborn_freighter"},
    )
    assert cfg.crew_npcs == []


def test_chassis_instance_with_crew() -> None:
    cfg = ChassisInstanceConfig(
        id="kestrel",
        name="Kestrel",
        **{"class": "voidborn_freighter"},
        crew_npcs=["kestrel_captain", "kestrel_engineer", "kestrel_doc", "kestrel_cook"],
    )
    assert cfg.crew_npcs == [
        "kestrel_captain", "kestrel_engineer", "kestrel_doc", "kestrel_cook",
    ]
