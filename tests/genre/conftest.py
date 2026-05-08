"""Shared fixtures for tests/genre/ test suite."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

# Canonical minimal pack fixture used as the base for clone-based tests.
_FIXTURE_PACK = Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "test_genre"


class MinimalPack:
    """A temporary copy of the test_genre fixture pack with mutable YAML overrides.

    Usage::

        pack = minimal_pack_factory(tmp_path)
        pack.set_rules_yaml(confrontations=[...], allowed_classes=["Fighter"])
        pack.set_classes_yaml([{"id": "fighter", ...}])
        load_genre_pack(pack.path)
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def set_rules_yaml(
        self,
        *,
        confrontations: list[dict[str, Any]],
        allowed_classes: list[str],
    ) -> None:
        """Write a minimal rules.yaml with the given confrontations and allowed_classes.

        All other required RulesConfig fields are set to safe defaults.
        """
        data: dict[str, Any] = {
            "tone": "test",
            "lethality": "low",
            "magic_level": "none",
            "stat_generation": "point_buy",
            "point_buy_budget": 27,
            "ability_score_names": ["STR", "DEX", "CON", "INT", "WIS", "CHA"],
            "allowed_classes": allowed_classes,
            "allowed_races": [],
            "confrontations": confrontations,
        }
        rules_path = self.path / "rules.yaml"
        with rules_path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def set_classes_yaml(self, classes: list[dict[str, Any]]) -> None:
        """Write classes.yaml with the given class definition dicts."""
        classes_path = self.path / "classes.yaml"
        with classes_path.open("w", encoding="utf-8") as f:
            yaml.dump(classes, f, default_flow_style=False, sort_keys=False)


@pytest.fixture
def minimal_pack_factory():
    """Factory fixture: call with (tmp_path) to get a MinimalPack.

    The returned pack is a full clone of tests/fixtures/packs/test_genre with
    its lethality_policy.yaml genre_key updated to match the tmp directory name.
    Call set_rules_yaml() / set_classes_yaml() to inject test-specific YAML.
    """

    def _factory(tmp_path: Path) -> MinimalPack:
        dest = tmp_path / "test_pack"
        shutil.copytree(_FIXTURE_PACK, dest)
        # Update lethality_policy.yaml genre_key to match the new directory name.
        lethality_yaml = dest / "lethality_policy.yaml"
        if lethality_yaml.exists():
            with lethality_yaml.open("r", encoding="utf-8") as f:
                policy_data = yaml.safe_load(f) or {}
            policy_data["genre_key"] = dest.name
            with lethality_yaml.open("w", encoding="utf-8") as f:
                yaml.dump(policy_data, f, default_flow_style=False, sort_keys=False)
        return MinimalPack(dest)

    return _factory
