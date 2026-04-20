"""Tests for world model types."""

from __future__ import annotations

import pytest

from sidequest.genre.models import CartographyConfig, NavigationMode, Region, WorldConfig


class TestWorldConfig:
    def test_extra_allowed(self) -> None:
        """WorldConfig uses flatten extras — unknown fields should be accepted."""
        wc = WorldConfig.model_validate({
            "name": "Test World",
            "description": "A test",
            "custom_field": "extra value",
        })
        assert wc.name == "Test World"

    def test_roundtrip(self) -> None:
        wc = WorldConfig(name="Test", description="Desc")
        data = wc.model_dump()
        wc2 = WorldConfig.model_validate(data)
        assert wc2.name == "Test"


class TestCartographyConfig:
    def test_defaults(self) -> None:
        cart = CartographyConfig(
            world_name="Test",
            starting_region="start",
            map_style="basic",
        )
        assert cart.navigation_mode == NavigationMode.region
        assert cart.regions == {}

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            CartographyConfig.model_validate({
                "world_name": "T", "starting_region": "s",
                "map_style": "b", "navigation_mode": "region",
                "bogus": True,
            })

    def test_roundtrip(self) -> None:
        cart = CartographyConfig(
            world_name="Test",
            starting_region="start",
            map_style="basic",
            navigation_mode=NavigationMode.room_graph,
        )
        data = cart.model_dump()
        cart2 = CartographyConfig.model_validate(data)
        assert cart2.navigation_mode == NavigationMode.room_graph


class TestRegion:
    def test_extra_allowed(self) -> None:
        """Region uses flatten extras — extra fields accepted."""
        r = Region.model_validate({
            "name": "Forest",
            "summary": "A dense forest",
            "description": "Trees everywhere",
            "chase_profile": {"speed": 3},
        })
        assert r.name == "Forest"

    def test_roundtrip(self) -> None:
        r = Region(name="Town", summary="A town", description="Busy")
        data = r.model_dump()
        r2 = Region.model_validate(data)
        assert r2.name == "Town"
