from __future__ import annotations

import pytest

from sidequest.game.encounter import MetricDirection
from sidequest.genre.models.rules import (
    BeatDef,
    ConfrontationDef,
    MetricDef,
)
from sidequest.server.dispatch.confrontation import find_confrontation_def


def _def(confrontation_type: str, label: str, category: str) -> ConfrontationDef:
    return ConfrontationDef(
        type=confrontation_type,
        label=label,
        category=category,
        metric=MetricDef(
            name="hp",
            direction="descending",
            starting=10,
            threshold_low=0,
        ),
        beats=[BeatDef(id="attack", label="Attack", metric_delta=1, stat_check="strength")],
    )


def test_find_confrontation_def_returns_match_by_type() -> None:
    defs = [_def("combat", "Dungeon Combat", "combat"),
            _def("chase", "Corridor Pursuit", "movement")]
    match = find_confrontation_def(defs, "combat")
    assert match is not None
    assert match.confrontation_type == "combat"
    assert match.label == "Dungeon Combat"


def test_find_confrontation_def_returns_none_when_missing() -> None:
    defs = [_def("combat", "Combat", "combat")]
    assert find_confrontation_def(defs, "duel") is None


def test_find_confrontation_def_is_case_sensitive() -> None:
    defs = [_def("combat", "Combat", "combat")]
    assert find_confrontation_def(defs, "Combat") is None
