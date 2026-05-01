"""Tests for location-anchored opening directive (Aureate Span)."""

from __future__ import annotations

from sidequest.genre.models.authored_npc import AuthoredNpc
from sidequest.genre.models.narrative import (
    Opening,
    OpeningSetting,
    OpeningTone,
    OpeningTrigger,
)
from sidequest.server.dispatch.opening import _render_directive_location


def _make_opening() -> Opening:
    return Opening(
        id="solo_arena_trial",
        name="Sand on the Threshold",
        triggers=OpeningTrigger(mode="solo"),
        setting=OpeningSetting(
            location_label="the Imperatrix's Arena, threshold gate",
            situation="Pre-bout assembly; the crowd's noise already a wall.",
            present_npcs=["arena_master"],
        ),
        tone=OpeningTone(
            register="operatic, gilded, charged",
            stakes="imminent — in-medias-res by design",
            avoid_at_all_costs=["ending the turn with a question"],
        ),
        establishing_narration="The crowd noise hits you like a wall. The sand is already stained.",
        first_turn_invitation="Someone shoves you forward.",
    )


def _make_present_npcs() -> list[AuthoredNpc]:
    return [
        AuthoredNpc(
            id="arena_master",
            name="ArenaMasterName",
            role="arena master",
            appearance="gilded mask, no visible face",
            initial_disposition=0,
        ),
    ]


def test_location_render_includes_location_label() -> None:
    out = _render_directive_location(
        opening=_make_opening(),
        present_npcs=_make_present_npcs(),
        magic_register="",
        per_pc_beat=None,
    )
    assert "the Imperatrix's Arena, threshold gate" in out
    assert "aboard the" not in out


def test_location_render_omits_chassis_voice_block() -> None:
    out = _render_directive_location(
        opening=_make_opening(),
        present_npcs=_make_present_npcs(),
        magic_register="",
        per_pc_beat=None,
    )
    assert "CHASSIS VOICE" not in out


def test_location_render_lists_present_npcs() -> None:
    out = _render_directive_location(
        opening=_make_opening(),
        present_npcs=_make_present_npcs(),
        magic_register="",
        per_pc_beat=None,
    )
    assert "ArenaMasterName" in out


def test_location_render_includes_avoid_list() -> None:
    out = _render_directive_location(
        opening=_make_opening(),
        present_npcs=_make_present_npcs(),
        magic_register="",
        per_pc_beat=None,
    )
    assert "ending the turn with a question" in out


def test_location_render_first_turn_invitation_at_close() -> None:
    op = _make_opening()
    out = _render_directive_location(
        opening=op,
        present_npcs=_make_present_npcs(),
        magic_register="",
        per_pc_beat=None,
    )
    inv_idx = out.find(op.first_turn_invitation)
    narr_idx = out.find(op.establishing_narration)
    assert narr_idx < inv_idx
