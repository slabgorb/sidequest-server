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
    NpcRegistryEntry,
    TurnManager,
)
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from sidequest.server.session_helpers import _detect_npc_identity_drift
from sidequest.server.watcher import WatcherSpanProcessor
from sidequest.telemetry import spans as spans_module
from sidequest.telemetry.watcher_hub import watcher_hub


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
        npc_registry=[],
        quest_log={},
        lore_established=[],
        characters=[],
        turn_manager=TurnManager(),
    )
    snapshot.turn_manager.record_interaction()

    result = NarrationTurnResult(
        narration="She waves.",
        npcs_present=[
            NpcMention(name="Vex", pronouns="she/her", role="scavenger", appearance="")
        ],
    )
    _apply_narration_result_to_snapshot(snapshot, result, player_name="Rux")
    await asyncio.sleep(0.05)

    # Snapshot must have been mutated inside the span.
    assert len(snapshot.npc_registry) == 1
    assert snapshot.npc_registry[0].name == "Vex"

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
async def test_npc_reinvented_emits_warning_state_transition_via_span_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the narrator's NPC mention disagrees with the canonical
    registry entry (identity drift), the production helper must emit a
    routed ``state_transition`` with ``severity=warning`` —
    proving both that ``_detect_npc_identity_drift`` opens the span and
    that the WatcherSpanProcessor propagates the severity attribute."""
    captured = await _setup(monkeypatch, "test-npc-reinvented-wiring")

    existing = NpcRegistryEntry(
        name="Vex",
        role="scavenger",
        pronouns="she/her",
        appearance=None,
        last_seen_location="Tood's Dome",
        last_seen_turn=1,
    )
    drift_mention = NpcMention(
        name="Vex",
        pronouns="they/them",  # disagrees with canonical "she/her"
        role="scavenger",
        appearance="",
    )

    _detect_npc_identity_drift(existing, drift_mention, turn_num=9)
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
