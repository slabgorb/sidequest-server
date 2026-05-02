"""Tests for confrontation-def lookup and payload assembly.

Task 12 (2026-04-25): Rewritten for dual-dial schema — ConfrontationDef now
requires player_metric + opponent_metric; BeatDef now uses kind + base.
"""

from __future__ import annotations

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    StructuredEncounter,
)
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
        player_metric=MetricDef(name="momentum", starting=0, threshold=10),
        opponent_metric=MetricDef(name="momentum", starting=0, threshold=10),
        beats=[
            BeatDef.model_validate(
                {
                    "id": "attack",
                    "label": "Attack",
                    "kind": "strike",
                    "base": 1,
                    "stat_check": "STR",
                }
            )
        ],
    )


def _encounter(*, mood_override: str | None = None) -> StructuredEncounter:
    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        structured_phase=EncounterPhase.Setup,
        actors=[
            EncounterActor(name="Rux", role="combatant", side="player"),
        ],
    )
    if mood_override is not None:
        enc.mood_override = mood_override
    return enc


def test_find_confrontation_def_returns_match_by_type() -> None:
    defs = [
        _def("combat", "Dungeon Combat", "combat"),
        _def("chase", "Corridor Pursuit", "movement"),
    ]
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
    enc = _encounter()
    payload = build_confrontation_payload(
        encounter=enc, cdef=cdef, genre_slug="caverns_and_claudes"
    )
    assert payload["type"] == "combat"
    assert payload["label"] == "Dungeon Combat"
    assert payload["category"] == "combat"
    assert payload["genre_slug"] == "caverns_and_claudes"
    assert payload["active"] is True
    assert [a["name"] for a in payload["actors"]] == ["Rux"]
    assert payload["player_metric"]["current"] == 0
    assert payload["opponent_metric"]["current"] == 0
    assert [b["id"] for b in payload["beats"]] == ["attack"]
    assert isinstance(payload["mood"], str)


def test_build_confrontation_payload_uses_encounter_mood_override_when_set() -> None:
    cdef = _def("combat", "Dungeon Combat", "combat")
    cdef = cdef.model_copy(update={"mood": "pack-mood"})
    enc = _encounter(mood_override="panic")
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


def test_build_confrontation_payload_empty_string_override_preserved() -> None:
    cdef = _def("combat", "Dungeon Combat", "combat")
    cdef = cdef.model_copy(update={"mood": "pack-mood"})
    enc = _encounter(mood_override="")
    payload = build_confrontation_payload(
        encounter=enc, cdef=cdef, genre_slug="caverns_and_claudes"
    )
    # Empty-string override is still an override — do NOT fall through to cdef.mood.
    assert payload["mood"] == ""


def test_build_confrontation_payload_both_none_defaults_to_empty_string() -> None:
    cdef = _def("combat", "Dungeon Combat", "combat")
    # cdef.mood is None by default and encounter has no override.
    enc = _encounter()
    payload = build_confrontation_payload(
        encounter=enc, cdef=cdef, genre_slug="caverns_and_claudes"
    )
    # UI contract: mood is a non-nullable string.
    assert isinstance(payload["mood"], str)
    assert payload["mood"] == ""
