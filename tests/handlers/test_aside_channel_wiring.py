"""ADR-107 — mandatory MP wiring test (RED, story 50-25). THE CENTERPIECE.

Plan: docs/superpowers/plans/2026-05-17-aside-channel.md Task 5.
Spec: docs/superpowers/specs/2026-05-17-aside-channel-design.md §7.

Drives the REAL handler path and asserts ALL SEVEN out-of-band
guarantees — this is the wiring test that proves the aside channel is
connected end-to-end (CLAUDE.md "Every Test Suite Needs a Wiring Test"),
not merely that the resolver works in isolation.

RED reason (known/planned): imports `tests.handlers._harness`, which
does NOT yet exist. Per the plan's Task 5 harness note, Dev factors the
3-player MP room/snapshot setup out of the existing sibling handler test
(e.g. tests/handlers/test_player_action_speech_broadcast.py) into that
shared module during GREEN, then wires player_action.py (Task 4) until
every assertion below passes. This currently fails on ImportError; that
is the correct RED state for a not-yet-wired feature.
"""

import pytest

from sidequest.protocol.enums import MessageType

# Reuse the existing handler-test harness. Dev creates tests/handlers/_harness.py
# in GREEN by factoring the 3-player MP fixture out of the sibling handler test
# (small refactor, no behavior change) so this test and the sibling share it.
from tests.handlers._harness import make_mp_room, submit, fake_aside_llm


@pytest.mark.asyncio
async def test_aside_is_out_of_band_in_mp():
    room = make_mp_room(
        players=["Carl", "Donut", "Katia"],
        llm_aside=fake_aside_llm(
            '{"answer":"Knee-deep — wade, no carry.","outcome":"answered",'
            '"grounded_on":["character.size","region.water_depth"]}'
        ),
    )

    nlog_before = room.narrative_log_count()
    scrap_before = room.scrapbook_count()
    turn_before = room.turn_round()

    # Carl submits a real action; Katia fires an aside mid-round; Donut pending.
    await submit(room, "Carl", "I open the door", aside=False)
    aside_out = await submit(
        room, "Katia", "can I wade or must I be carried?", aside=True
    )

    # (1)(2)(3)(4) no turn record / no world advance
    assert room.narrative_log_count() == nlog_before
    assert room.scrapbook_count() == scrap_before
    assert room.turn_round() == turn_before
    assert room.world_patch_count() == 0

    # (5) barrier still waiting on Katia's real action + Donut unaffected
    assert not room.barrier_fired()
    assert room.pending_player_ids() == {"Katia", "Donut"}  # Carl submitted

    # (6) ASIDE_ANSWER broadcast to ALL seats (table-visible, spec §5)
    assert aside_out and aside_out[0].type == MessageType.ASIDE_ANSWER
    assert room.last_broadcast_recipients() == {"Carl", "Donut", "Katia"}

    # (7) the aside.resolve span fired (the lie-detector, CLAUDE.md OTEL)
    assert room.spans_named("aside.resolve")

    # Katia now submits her real action -> barrier fires normally; the aside
    # did not pay her turn debt.
    await submit(room, "Katia", "I wade in", aside=False)
    await submit(room, "Donut", "I follow", aside=False)
    assert room.barrier_fired()
    assert room.turn_round() == turn_before + 1
