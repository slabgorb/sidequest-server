"""Tests for lore model types."""

from __future__ import annotations

from sidequest.genre.models import Faction, Lore, WorldLore


class TestFaction:
    def test_valid(self) -> None:
        f = Faction(name="The Guard", summary="Town militia", description="Keeps order.")
        assert f.name == "The Guard"

    def test_extra_allowed(self) -> None:
        """Faction uses flatten extras."""
        f = Faction.model_validate(
            {
                "name": "F",
                "summary": "S",
                "description": "D",
                "agenda": "control everything",
            }
        )
        assert f.name == "F"

    def test_roundtrip(self) -> None:
        f = Faction(name="X", summary="Y", description="Z", disposition="neutral")
        data = f.model_dump()
        f2 = Faction.model_validate(data)
        assert f2.disposition == "neutral"


class TestLore:
    def test_valid(self) -> None:
        lore = Lore(
            world_name="Test",
            history="Long ago...",
            geography="Mountains",
            cosmology="Stars",
        )
        assert lore.world_name == "Test"

    def test_extra_allowed(self) -> None:
        """Lore uses flatten extras (setting_anchor, themes, etc.)"""
        lore = Lore.model_validate(
            {
                "world_name": "W",
                "history": "H",
                "geography": "G",
                "cosmology": "C",
                "setting_anchor": "The dungeon is the world.",
                "themes": ["darkness", "greed"],
            }
        )
        assert lore.world_name == "W"

    def test_roundtrip(self) -> None:
        lore = Lore(world_name="W", history="H", geography="G", cosmology="C")
        data = lore.model_dump()
        lore2 = Lore.model_validate(data)
        assert lore2.world_name == "W"


class TestWorldLore:
    def test_all_optional(self) -> None:
        wl = WorldLore()
        assert wl.world_name is None

    def test_extra_allowed(self) -> None:
        wl = WorldLore.model_validate({"setting": "Bleak", "faction_relations": {}})
        assert wl is not None
