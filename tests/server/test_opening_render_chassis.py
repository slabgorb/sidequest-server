"""Tests for the chassis-anchored opening directive renderer."""

from __future__ import annotations

import pytest

from sidequest.genre.models.authored_npc import AuthoredNpc
from sidequest.genre.models.chassis import (
    BondTier,
    ChassisVoiceSpec,
)
from sidequest.genre.models.narrative import (
    Opening,
    OpeningSetting,
    OpeningTone,
    OpeningTrigger,
)
from sidequest.genre.models.rigs_world import (
    BondSeed,
    ChassisInstanceConfig,
    OceanScores,
)
from sidequest.server.dispatch.opening import _render_directive_chassis


def _make_kestrel() -> ChassisInstanceConfig:
    return ChassisInstanceConfig(
        id="kestrel",
        name="Kestrel",
        **{"class": "voidborn_freighter"},
        OCEAN=OceanScores(O=0.6, C=0.7, E=0.4, A=0.5, N=0.5),
        voice=ChassisVoiceSpec(
            default_register="dry_warm",
            vocal_tics=["theatrical sigh", "almost-but-legally-distinct from a laugh"],
            silence_register="approving_or_sulking",
            name_forms_by_bond_tier={
                "severed": "Pilot",
                "hostile": "Pilot",
                "strained": "Pilot",
                "neutral": "Pilot",
                "familiar": "Mr. {last_name}",
                "trusted": "{first_name}",
                "fused": "{nickname}",
            },
        ),
        interior_rooms=["galley", "cockpit", "engineering"],
        bond_seeds=[BondSeed(
            character_role="player_character",
            bond_strength_character_to_chassis=0.45,
            bond_strength_chassis_to_character=0.45,
            bond_tier_character="trusted",
            bond_tier_chassis="trusted",
            history_seeds=["three jumps' worth of patch kits"],
        )],
        crew_npcs=["captain_x", "engineer_y"],
    )


def _make_authored_crew() -> list[AuthoredNpc]:
    return [
        AuthoredNpc(
            id="captain_x", name="CaptainName",
            role="captain", appearance="weathered",
            history_seeds=["flew Hegemony patrol once"],
            initial_disposition=60,
        ),
        AuthoredNpc(
            id="engineer_y", name="EngineerName",
            role="engineer", appearance="grease-stained jumpsuit",
            initial_disposition=55,
        ),
    ]


def _make_opening() -> Opening:
    return Opening(
        id="solo_galley_morning",
        name="Galley, Morning Coast",
        triggers=OpeningTrigger(mode="solo", backgrounds=["Far Landing Raised Me"]),
        setting=OpeningSetting(
            chassis_instance="kestrel",
            interior_room="galley",
            situation="Inbound for Far Landing, an hour out.",
        ),
        tone=OpeningTone(
            register="warm, lived-in, dry",
            stakes="none on turn 1",
            avoid_at_all_costs=["any confrontation", "any dice roll"],
        ),
        establishing_narration="The galley is warm. The fan ticks once every few seconds.",
        first_turn_invitation="Outside the porthole: void, stars.",
    )


def test_chassis_render_includes_setting_block() -> None:
    out = _render_directive_chassis(
        opening=_make_opening(),
        chassis=_make_kestrel(),
        authored_crew=_make_authored_crew(),
        magic_register="The Reach doesn't perform miracles. It bleeds through.",
        bond_tier_for_pc="trusted",
        per_pc_beat=None,
        pc_first_name="Zanzibar",
        pc_last_name="Jones",
        pc_nickname="",
    )
    assert "=== OPENING SCENARIO ===" in out
    assert "=== END OPENING ===" in out
    assert "aboard the Kestrel" in out
    assert "Galley" in out  # interior_room display name


def test_chassis_render_resolves_name_form() -> None:
    """trusted bond_tier → '{first_name}' template → 'Zanzibar'."""
    out = _render_directive_chassis(
        opening=_make_opening(),
        chassis=_make_kestrel(),
        authored_crew=_make_authored_crew(),
        magic_register="Reach register text",
        bond_tier_for_pc="trusted",
        per_pc_beat=None,
        pc_first_name="Zanzibar",
        pc_last_name="Jones",
        pc_nickname="",
    )
    assert "Zanzibar" in out
    assert "{first_name}" not in out


def test_chassis_render_includes_establishing_narration() -> None:
    op = _make_opening()
    out = _render_directive_chassis(
        opening=op,
        chassis=_make_kestrel(),
        authored_crew=_make_authored_crew(),
        magic_register="Reach register text",
        bond_tier_for_pc="trusted",
        per_pc_beat=None,
        pc_first_name="Z", pc_last_name="J", pc_nickname="",
    )
    assert op.establishing_narration in out
    assert "ESTABLISHING NARRATION" in out


def test_chassis_render_includes_avoid_list() -> None:
    out = _render_directive_chassis(
        opening=_make_opening(),
        chassis=_make_kestrel(),
        authored_crew=_make_authored_crew(),
        magic_register="Reach register text",
        bond_tier_for_pc="trusted",
        per_pc_beat=None,
        pc_first_name="Z", pc_last_name="J", pc_nickname="",
    )
    assert "any confrontation" in out
    assert "any dice roll" in out


def test_chassis_render_lists_crew_npcs() -> None:
    out = _render_directive_chassis(
        opening=_make_opening(),
        chassis=_make_kestrel(),
        authored_crew=_make_authored_crew(),
        magic_register="Reach register text",
        bond_tier_for_pc="trusted",
        per_pc_beat=None,
        pc_first_name="Z", pc_last_name="J", pc_nickname="",
    )
    assert "CaptainName" in out
    assert "EngineerName" in out
    assert "PRE-LOADED NPCS PRESENT" in out


def test_chassis_render_omits_party_framing_when_solo() -> None:
    out = _render_directive_chassis(
        opening=_make_opening(),
        chassis=_make_kestrel(),
        authored_crew=_make_authored_crew(),
        magic_register="Reach register text",
        bond_tier_for_pc="trusted",
        per_pc_beat=None,
        pc_first_name="Z", pc_last_name="J", pc_nickname="",
    )
    assert "PARTY FRAMING" not in out


def test_chassis_render_first_turn_invitation_at_close() -> None:
    op = _make_opening()
    out = _render_directive_chassis(
        opening=op,
        chassis=_make_kestrel(),
        authored_crew=_make_authored_crew(),
        magic_register="Reach register text",
        bond_tier_for_pc="trusted",
        per_pc_beat=None,
        pc_first_name="Z", pc_last_name="J", pc_nickname="",
    )
    inv_idx = out.find(op.first_turn_invitation)
    narr_idx = out.find(op.establishing_narration)
    assert narr_idx < inv_idx, "first_turn_invitation should land near the close"
