"""End-to-end wiring for the NPC Phase 2 bundle.

Covers both routed NPC spans through their real production call sites:
  * ``SPAN_NPC_AUTO_REGISTERED`` via ``_apply_narration_result_to_snapshot``
  * ``SPAN_NPC_REINVENTED`` via ``_detect_npc_identity_drift``

Each test asserts the typed ``state_transition`` event reaches the hub
through ``SPAN_ROUTES`` (proving production code opens the helper, not
a leftover ``publish_event`` call). The reinvented test additionally
asserts ``severity=warning`` propagates via the span-attribute escape
hatch added to ``WatcherSpanProcessor``.

Per ``CLAUDE.md`` "Verify Wiring, Not Just Existence".
"""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace import TracerProvider

from sidequest.agents.orchestrator import NarrationTurnResult, NpcMention
from sidequest.game.session import (
    GameSnapshot,
    TurnManager,
)
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from sidequest.server.session_helpers import _detect_npc_identity_drift
from sidequest.server.watcher import WatcherSpanProcessor
from sidequest.telemetry import spans as spans_module
from sidequest.telemetry.watcher_hub import watcher_hub
from tests._helpers.session_room import room_for


async def _setup(monkeypatch: pytest.MonkeyPatch, label: str) -> list[dict]:
    """Prepare the module hub + a local processor and return the
    captured events list. Same shape as the state-patch wiring test —
    avoids OTEL's "provider already installed" guard by patching
    ``spans_module.tracer`` instead of ``trace.set_tracer_provider``."""
    watcher_hub.bind_loop(asyncio.get_running_loop())
    async with watcher_hub._lock:  # noqa: SLF001
        watcher_hub._subscribers.clear()  # noqa: SLF001

    captured: list[dict] = []

    class _Sock:
        async def send_json(self, data: dict) -> None:
            captured.append(data)

    await watcher_hub.subscribe(_Sock())  # type: ignore[arg-type]

    provider = TracerProvider()
    provider.add_span_processor(WatcherSpanProcessor(watcher_hub))
    local_tracer = provider.get_tracer(label)
    monkeypatch.setattr(spans_module, "tracer", lambda: local_tracer)

    return captured


@pytest.mark.asyncio
async def test_npc_auto_registered_emits_state_transition_via_span_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-mention NPC must reach the hub as a routed
    ``state_transition`` (component=npc_registry, op=auto_registered),
    proving ``narration_apply.py`` opens ``npc_auto_registered_span``
    rather than publishing directly."""
    captured = await _setup(monkeypatch, "test-npc-auto-wiring")

    snapshot = GameSnapshot(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        location="Tood's Dome",
        discovered_regions=["Tood's Dome"],
        quest_log={},
        lore_established=[],
        characters=[],
        turn_manager=TurnManager(),
    )
    snapshot.turn_manager.record_interaction()

    result = NarrationTurnResult(
        narration="She waves.",
        npcs_present=[NpcMention(name="Vex", pronouns="she/her", role="scavenger", appearance="")],
    )
    _apply_narration_result_to_snapshot(
        snapshot, result, player_name="Rux", room=room_for(snapshot)
    )
    await asyncio.sleep(0.05)

    # Snapshot must have been mutated inside the span. Wave 2A: novel
    # narrator-invented NPCs append to ``npc_pool`` (the unified Npc
    # store), not the legacy ``npc_registry``. The auto-registered span
    # still fires; ``registry_len`` reflects pool length.
    assert len(snapshot.npc_pool) == 1
    assert snapshot.npc_pool[0].name == "Vex"

    typed = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "npc_registry"
        and e["fields"].get("op") == "auto_registered"
    ]
    assert len(typed) == 1, (
        "expected exactly one auto_registered state_transition "
        f"(got {len(typed)}: {[e['fields'] for e in typed]})"
    )
    fields = typed[0]["fields"]
    assert fields["name"] == "Vex"
    assert fields["pronouns"] == "she/her"
    assert fields["role"] == "scavenger"
    assert fields["registry_len"] == 1
    assert typed[0]["severity"] == "info"


@pytest.mark.asyncio
async def test_pc_name_in_npcs_present_does_not_register_and_emits_skip_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: 2026-04-29 multiplayer playtest. The MP joiner-orientation
    auto-narration named the host PC (``Laverne``) and the auto-register
    loop promoted that PC into the NPC registry as ``role=ally``. Once a
    PC is in the NPC registry, downstream beat-selection and party-state
    queries treat them as an NPC ally rather than the player they are.

    The fix in ``narration_apply.py`` consults the snapshot's PC roster
    (case-folded equality on ``character.core.name``) before registering,
    skips PC-name matches, and emits ``npc.pc_name_skipped`` so the GM
    panel can see the filter fire (Sebastien needs the visibility — the
    narrator naming party members in NPC contexts is itself a signal).
    """
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    captured = await _setup(monkeypatch, "test-npc-pc-name-skip-wiring")

    laverne = Character(
        core=CreatureCore(
            name="Laverne",
            description="Smuggler/Spacer",
            personality="cool under fire",
            inventory=Inventory(),
            statuses=[],
        ),
        char_class="Smuggler",
        race="Human",
        backstory="Pilots the Coyote's Tooth.",
    )
    snapshot = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="The Bridge of the Coyote's Tooth",
        discovered_regions=["The Bridge"],
        quest_log={},
        lore_established=[],
        characters=[laverne],
        turn_manager=TurnManager(),
    )
    snapshot.turn_manager.record_interaction()  # turn 2 (joiner orientation)

    # Same shape as the live playtest: narrator names the host PC in the
    # joiner's orientation narration; ``npcs_present`` includes them
    # because the extractor doesn't distinguish PC vs NPC mentions.
    result = NarrationTurnResult(
        narration="Laverne is in the pilot's couch, hands flat on her thighs.",
        npcs_present=[
            NpcMention(name="Laverne", pronouns="she/her", role="ally", appearance=""),
        ],
    )
    _apply_narration_result_to_snapshot(
        snapshot, result, player_name="Shirley", room=room_for(snapshot)
    )
    await asyncio.sleep(0.05)

    # Critical: pool must NOT have been mutated.
    assert snapshot.npc_pool == [], (
        f"PC-name auto-register filter failed — Laverne was promoted to NPC: {snapshot.npc_pool}"
    )

    # The skip span must reach the hub (GM panel visibility).
    skipped = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "npc_registry"
        and e["fields"].get("op") == "pc_name_skipped"
    ]
    assert len(skipped) == 1, (
        "expected exactly one pc_name_skipped state_transition "
        f"(got {len(skipped)}: {[e['fields'] for e in skipped]})"
    )
    fields = skipped[0]["fields"]
    assert fields["name"] == "Laverne"
    assert fields["matched_pc"] == "Laverne"
    assert fields["turn_number"] == 2

    # Auto-registered span must NOT have fired for the PC.
    auto_registered = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "npc_registry"
        and e["fields"].get("op") == "auto_registered"
    ]
    assert auto_registered == [], (
        "PC must not produce an auto_registered span — that's exactly the "
        f"bug we're fixing (got {[e['fields'] for e in auto_registered]})"
    )


@pytest.mark.asyncio
async def test_pc_name_filter_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PC-name filter folds case. Without folding, ``Laverne`` (PC)
    and ``laverne`` or ``LAVERNE`` (narration variation) would skip the
    filter and re-introduce the bug.
    """
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    captured = await _setup(monkeypatch, "test-npc-pc-name-skip-case-wiring")

    laverne = Character(
        core=CreatureCore(
            name="Laverne",
            description="Smuggler",
            personality="cool",
            inventory=Inventory(),
            statuses=[],
        ),
        char_class="Smuggler",
        race="Human",
        backstory=".",
    )
    snapshot = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="Bridge",
        discovered_regions=["Bridge"],
        quest_log={},
        lore_established=[],
        characters=[laverne],
        turn_manager=TurnManager(),
    )
    snapshot.turn_manager.record_interaction()
    result = NarrationTurnResult(
        narration="laverne nods.",
        npcs_present=[
            NpcMention(name="laverne", pronouns="", role="", appearance=""),
        ],
    )
    _apply_narration_result_to_snapshot(
        snapshot, result, player_name="Shirley", room=room_for(snapshot)
    )
    await asyncio.sleep(0.05)
    assert snapshot.npc_pool == []
    skipped = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "npc_registry"
        and e["fields"].get("op") == "pc_name_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0]["fields"]["matched_pc"] == "Laverne"  # canonical form returned


@pytest.mark.asyncio
async def test_npc_reinvented_emits_warning_state_transition_via_span_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the narrator's NPC mention disagrees with the canonical
    registry entry (identity drift), the production helper must emit a
    routed ``state_transition`` with ``severity=warning`` —
    proving both that ``_detect_npc_identity_drift`` opens the span and
    that the WatcherSpanProcessor propagates the severity attribute."""
    captured = await _setup(monkeypatch, "test-npc-reinvented-wiring")

    drift_mention = NpcMention(
        name="Vex",
        pronouns="they/them",  # disagrees with canonical "she/her"
        role="scavenger",
        appearance="",
    )

    _detect_npc_identity_drift(
        existing_name="Vex",
        existing_role="scavenger",
        existing_pronouns="she/her",
        mention=drift_mention,
        turn_num=9,
    )
    await asyncio.sleep(0.05)

    typed = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "npc_registry"
        and e["fields"].get("op") == "reinvented"
    ]
    assert len(typed) == 1, (
        "expected exactly one reinvented state_transition "
        f"(got {len(typed)}: {[e['fields'] for e in typed]})"
    )
    assert typed[0]["severity"] == "warning", (
        "severity span-attribute escape hatch failed — "
        "GM panel needs warning-grade signal for identity drift"
    )
    fields = typed[0]["fields"]
    assert fields["name"] == "Vex"
    assert fields["drift_field"] == "pronouns"
    assert fields["expected"] == "she/her"
    assert fields["narrator"] == "they/them"
    assert fields["turn_number"] == 9
