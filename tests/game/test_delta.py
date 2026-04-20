"""Tests for sidequest.game.delta.

Game-layer StateDelta — boolean change detection, not wire-layer data.
"""

from __future__ import annotations

from sidequest.game.delta import StateDelta, compute_delta, snapshot
from sidequest.game.session import GameSnapshot


def _base_state() -> GameSnapshot:
    return GameSnapshot(
        genre_slug="test",
        world_slug="world",
        location="The Mines",
        time_of_day="dusk",
        atmosphere="gloomy",
        current_region="Ironhold",
        quest_log={"Find Warden": "active"},
        notes=["note one"],
        active_stakes="survival",
        lore_established=["The mines run deep"],
        discovered_regions=["Ironhold"],
        discovered_routes=["North Road"],
    )


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def test_snapshot_captures_location():
    state = _base_state()
    s = snapshot(state)
    assert s.location == "The Mines"


def test_snapshot_captures_atmosphere():
    state = _base_state()
    s = snapshot(state)
    assert s.atmosphere == "gloomy"


# ---------------------------------------------------------------------------
# compute_delta — no changes
# ---------------------------------------------------------------------------


def test_delta_empty_when_states_equal():
    state = _base_state()
    before = snapshot(state)
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.is_empty()


# ---------------------------------------------------------------------------
# compute_delta — individual field changes
# ---------------------------------------------------------------------------


def test_delta_location_changed():
    state = _base_state()
    before = snapshot(state)
    state.location = "The Caverns"
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.location_changed()
    assert delta.new_location == "The Caverns"
    assert not delta.is_empty()


def test_delta_new_location_none_when_no_change():
    state = _base_state()
    before = snapshot(state)
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.new_location is None


def test_delta_quest_log_changed():
    state = _base_state()
    before = snapshot(state)
    state.quest_log["New Quest"] = "active"
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.quest_log_changed()


def test_delta_atmosphere_changed():
    state = _base_state()
    before = snapshot(state)
    state.atmosphere = "tense"
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.atmosphere_changed()


def test_delta_regions_changed():
    state = _base_state()
    before = snapshot(state)
    state.discovered_regions.append("Sunken Vale")
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.regions_changed()


def test_delta_notes_changed():
    state = _base_state()
    before = snapshot(state)
    state.notes.append("second note")
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.notes


def test_delta_lore_changed():
    state = _base_state()
    before = snapshot(state)
    state.lore_established.append("A second lore entry")
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.lore


def test_delta_time_of_day_changed():
    state = _base_state()
    before = snapshot(state)
    state.time_of_day = "dawn"
    after = snapshot(state)
    delta = compute_delta(before, after)
    assert delta.time_of_day


# ---------------------------------------------------------------------------
# StateDelta accessors
# ---------------------------------------------------------------------------


def test_state_delta_is_empty_all_false():
    d = StateDelta()
    assert d.is_empty()


def test_state_delta_not_empty_when_characters_true():
    d = StateDelta(characters=True)
    assert not d.is_empty()
    assert d.characters_changed()


def test_state_delta_npcs_changed():
    d = StateDelta(npcs=True)
    assert d.npcs_changed()


def test_state_delta_tropes_changed():
    d = StateDelta(tropes=True)
    assert d.tropes_changed()


def test_state_delta_json_roundtrip():
    d = StateDelta(characters=True, location=True, new_location="The Caverns")
    json_str = d.model_dump_json()
    back = StateDelta.model_validate_json(json_str)
    assert back.characters is True
    assert back.location is True
    assert back.new_location == "The Caverns"
