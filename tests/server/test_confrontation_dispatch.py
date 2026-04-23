from __future__ import annotations

from sidequest.game.encounter import StructuredEncounter
from sidequest.genre.models.rules import (
    BeatDef,
    ConfrontationDef,
    MetricDef,
)
from sidequest.server.dispatch.confrontation import (
    build_clear_confrontation_payload,
    build_confrontation_payload,
    find_confrontation_def,
)


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


def test_build_confrontation_payload_active_for_live_encounter() -> None:
    cdef = _def("combat", "Dungeon Combat", "combat")
    enc = StructuredEncounter.combat(combatants=["Rux", "Goblin"], hp=10)
    payload = build_confrontation_payload(
        encounter=enc, cdef=cdef, genre_slug="caverns_and_claudes"
    )
    assert payload["type"] == "combat"
    assert payload["label"] == "Dungeon Combat"
    assert payload["category"] == "combat"
    assert payload["genre_slug"] == "caverns_and_claudes"
    assert payload["active"] is True
    assert [a["name"] for a in payload["actors"]] == ["Rux", "Goblin"]
    assert payload["metric"]["current"] == 10
    assert [b["id"] for b in payload["beats"]] == ["attack"]
    assert payload["mood"] is None or isinstance(payload["mood"], str)


def test_build_confrontation_payload_uses_encounter_mood_override_when_set() -> None:
    cdef = _def("combat", "Dungeon Combat", "combat")
    cdef = cdef.model_copy(update={"mood": "pack-mood"})
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.mood_override = "panic"
    payload = build_confrontation_payload(
        encounter=enc, cdef=cdef, genre_slug="caverns_and_claudes"
    )
    assert payload["mood"] == "panic"  # encounter override wins over cdef.mood


def test_build_clear_confrontation_payload_signals_end() -> None:
    payload = build_clear_confrontation_payload(
        encounter_type="combat", genre_slug="caverns_and_claudes"
    )
    assert payload["active"] is False
    assert payload["type"] == "combat"
    assert payload["genre_slug"] == "caverns_and_claudes"
