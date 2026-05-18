"""ADR-108 — MP item attribution via per-recipient tagging.

Playtest 2026-05-17 ``coyote_star-mp``: every narrator-granted item
landed on ``snapshot.characters[0]`` (the host/first seat), a literal
single-player Rust-port artifact (ADR-082, ``narration_apply.py``
~line 1944). In sealed MP rounds (ADR-036) there is no "acting player"
and inventory is per-player (ADR-037), so the recipient must be an
explicit narrator-supplied signal resolved through the seated-PC
machinery.

These tests drive the production seam ``_apply_narration_result_to_snapshot``
(the function ``WebSocketSessionHandler._execute_narration_turn`` calls at
narration_apply wiring) and assert:

* all four lanes (gained/lost/discarded/consumed) honour a ``recipient``
  tag and land on that seated PC — never ``characters[0]``;
* an absent or non-seated recipient in a seated round degrades to the
  narrating socket's PC AND fires the loud ``inventory`` /
  ``recipient_missing`` watcher (the OTEL lie-detector);
* single-player / empty ``player_seats`` behaviour is unchanged
  (regression guard);
* the resolver is wired into the real apply path (per-applied-item
  ``item_recipient_resolved`` watcher reaches the hub).
"""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace import TracerProvider

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.session import GameSnapshot, TurnManager
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from sidequest.server.watcher import WatcherSpanProcessor
from sidequest.telemetry import spans as spans_module
from sidequest.telemetry.watcher_hub import watcher_hub
from tests._helpers.session_room import room_for


def _mk_char(name: str, *, items: list[dict] | None = None) -> Character:
    inv = Inventory()
    if items:
        inv.items = list(items)
    return Character(
        core=CreatureCore(
            name=name,
            description=f"{name}, coyote_star crew",
            personality="wry",
            inventory=inv,
            statuses=[],
        ),
        char_class="Operative",
        race="Human",
        backstory=f"{name} crews the salvage run.",
    )


def _item(name: str, *, category: str = "tool", state: str = "Carried") -> dict:
    slug = name.lower().replace(" ", "_")
    return {
        "id": f"narrator:{slug}",
        "name": name,
        "description": f"{name}, recovered on the run.",
        "category": category,
        "value": 0,
        "weight": 0.0,
        "rarity": "common",
        "narrative_weight": 0.5,
        "tags": [],
        "equipped": False,
        "quantity": 1,
        "uses_remaining": None,
        "state": state,
    }


def _two_seat_snapshot(
    *,
    ritali_items: list[dict] | None = None,
    catalina_items: list[dict] | None = None,
) -> GameSnapshot:
    """Ritali Veer is ``characters[0]`` (the host/first-seat bug magnet);
    Catalina Valentine is the second seat. ``player_seats`` maps
    player_id -> character.core.name per ADR-037."""
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="Corvette Hold",
        discovered_regions=["Corvette Hold"],
        quest_log={},
        lore_established=[],
        characters=[
            _mk_char("Ritali Veer", items=ritali_items),
            _mk_char("Catalina Valentine", items=catalina_items),
        ],
        turn_manager=TurnManager(),
    )
    snap.player_seats = {"p1": "Ritali Veer", "p2": "Catalina Valentine"}
    snap.turn_manager.record_interaction()
    return snap


async def _capture(monkeypatch: pytest.MonkeyPatch, label: str) -> list[dict]:
    """Subscribe a sink to the watcher hub and install a local
    TracerProvider so both the direct ``_watcher_publish`` recipient
    events and the routed aggregate span flow to ``captured``. Mirrors
    ``tests/integration/test_inventory_wiring.py``'s seam."""
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


def _inv_names(char: Character) -> list[str]:
    return [str(it.get("name", "")) for it in char.core.inventory.items]


def _recipient_missing_events(captured: list[dict]) -> list[dict]:
    return [
        e
        for e in captured
        if e["component"] == "inventory" and e["fields"].get("op") == "recipient_missing"
    ]


def _resolved_events(captured: list[dict]) -> list[dict]:
    return [
        e
        for e in captured
        if e["component"] == "inventory" and e["fields"].get("op") == "item_recipient_resolved"
    ]


# --------------------------------------------------------------------------
# 1-4: tagged recipient lands on the named seated PC, never characters[0]
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mp_items_gained_lands_on_tagged_recipient_not_characters0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _capture(monkeypatch, "adr108-gained")
    snap = _two_seat_snapshot()

    result = NarrationTurnResult(
        narration="The officer presses the chip into Catalina's palm.",
        items_gained=[
            {
                "name": "Station Map Chip",
                "description": "A dock-authority data wafer.",
                "category": "quest",
                "recipient": "Catalina Valentine",
            }
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="p1",
        room=room_for(snap),
        acting_character_name="Ritali Veer",
    )

    ritali, catalina = snap.characters
    assert "Station Map Chip" in _inv_names(catalina)
    assert "Station Map Chip" not in _inv_names(ritali), (
        "ADR-108: tagged item must NOT land on characters[0] (Ritali)"
    )


@pytest.mark.asyncio
async def test_mp_items_lost_lands_on_tagged_recipient_not_characters0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _capture(monkeypatch, "adr108-lost")
    snap = _two_seat_snapshot(catalina_items=[_item("Corvette Scan Data")])

    result = NarrationTurnResult(
        narration="Catalina hands the scan data to the broker and it's gone.",
        items_lost=[{"name": "Corvette Scan Data", "recipient": "Catalina Valentine"}],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="p1",
        room=room_for(snap),
        acting_character_name="Ritali Veer",
    )

    ritali, catalina = snap.characters
    assert "Corvette Scan Data" not in _inv_names(catalina), (
        "ADR-108: items_lost must resolve to the tagged recipient (Catalina)"
    )
    assert _inv_names(ritali) == []


@pytest.mark.asyncio
async def test_mp_items_discarded_lands_on_tagged_recipient_not_characters0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _capture(monkeypatch, "adr108-discarded")
    snap = _two_seat_snapshot(catalina_items=[_item("Cutting Torch", category="tool")])

    result = NarrationTurnResult(
        narration="Catalina sets the torch down on the deck and leaves it.",
        items_discarded=[{"name": "Cutting Torch", "recipient": "Catalina Valentine"}],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="p1",
        room=room_for(snap),
        acting_character_name="Ritali Veer",
    )

    _, catalina = snap.characters
    items = catalina.core.inventory.items
    assert len(items) == 1
    assert items[0]["state"] == "Discarded", (
        "ADR-108: items_discarded must transition the tagged recipient's item"
    )


@pytest.mark.asyncio
async def test_mp_items_consumed_lands_on_tagged_recipient_not_characters0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _capture(monkeypatch, "adr108-consumed")
    snap = _two_seat_snapshot(catalina_items=[_item("Medpatch", category="consumable")])

    result = NarrationTurnResult(
        narration="Catalina slaps the medpatch on and it's spent.",
        items_consumed=[{"name": "Medpatch", "recipient": "Catalina Valentine"}],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="p1",
        room=room_for(snap),
        acting_character_name="Ritali Veer",
    )

    _, catalina = snap.characters
    assert _inv_names(catalina) == [], (
        "ADR-108: items_consumed must remove the tagged recipient's item"
    )


# --------------------------------------------------------------------------
# 5-6: absent / non-seated recipient -> narrating socket PC + loud watcher
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_absent_recipient_lands_on_narrating_pc_and_fires_recipient_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = await _capture(monkeypatch, "adr108-absent")
    snap = _two_seat_snapshot()

    result = NarrationTurnResult(
        narration="A coin purse is recovered from the wreck.",
        items_gained=[{"name": "Coin Purse", "category": "treasure"}],  # no recipient
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="p2",
        room=room_for(snap),
        acting_character_name="Catalina Valentine",  # narrating socket's PC
    )
    await asyncio.sleep(0.05)  # drain the async watcher publish

    ritali, catalina = snap.characters
    assert "Coin Purse" in _inv_names(catalina), (
        "absent recipient must degrade to the narrating socket's PC (Catalina)"
    )
    assert "Coin Purse" not in _inv_names(ritali), (
        "absent recipient must NEVER fall back to characters[0] (Ritali)"
    )

    missing = _recipient_missing_events(captured)
    assert len(missing) == 1, (
        f"expected one loud inventory/recipient_missing watcher, got {len(missing)}"
    )
    f = missing[0]["fields"]
    assert f["resolution_mode"] == "recipient_missing"
    assert f["lane"] == "gained"
    assert f["item"] == "Coin Purse"
    assert f["fallback_recipient"] == "Catalina Valentine"
    assert missing[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_non_seated_recipient_lands_on_narrating_pc_and_fires_recipient_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = await _capture(monkeypatch, "adr108-nonseated")
    snap = _two_seat_snapshot()

    result = NarrationTurnResult(
        narration="Lieutenant Ortega is handed the manifest — but Ortega is an NPC.",
        items_gained=[
            {
                "name": "Cargo Manifest",
                "category": "quest",
                "recipient": "Lieutenant Ortega",  # not a seated PC
            }
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="p1",
        room=room_for(snap),
        acting_character_name="Ritali Veer",  # narrating socket's PC
    )
    await asyncio.sleep(0.05)  # drain the async watcher publish

    ritali, catalina = snap.characters
    assert "Cargo Manifest" in _inv_names(ritali), (
        "non-seated recipient must degrade to the narrating socket's PC (Ritali)"
    )
    assert "Cargo Manifest" not in _inv_names(catalina)

    missing = _recipient_missing_events(captured)
    assert len(missing) == 1
    f = missing[0]["fields"]
    assert f["resolution_mode"] == "non_seated_recipient"
    assert f["offered_recipient"] == "Lieutenant Ortega"
    assert f["fallback_recipient"] == "Ritali Veer"
    assert missing[0]["severity"] == "warning"


# --------------------------------------------------------------------------
# 7: single-player / empty player_seats — unchanged behaviour (regression)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_player_empty_seats_lone_pc_still_receives_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = await _capture(monkeypatch, "adr108-single")
    snap = GameSnapshot(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        location="Tood's Dome",
        discovered_regions=["Tood's Dome"],
        quest_log={},
        lore_established=[],
        characters=[_mk_char("Rux")],
        turn_manager=TurnManager(),
    )
    # player_seats intentionally empty (pre-MP / single-player save).
    snap.turn_manager.record_interaction()

    result = NarrationTurnResult(
        narration="Rux finds a spanner.",
        items_gained=[{"name": "Rusty Spanner", "category": "tool"}],  # no recipient
    )
    _apply_narration_result_to_snapshot(snap, result, player_name="Rux", room=room_for(snap))
    await asyncio.sleep(0.05)  # drain the async watcher publish

    assert "Rusty Spanner" in _inv_names(snap.characters[0]), (
        "single-player lone PC must still receive (behaviour unchanged)"
    )
    # Empty seats is NOT a contract violation — the loud watcher must
    # stay silent so single-player saves don't pollute the GM panel.
    assert _recipient_missing_events(captured) == []
    # The per-applied-item observability event still fires (resolved as
    # the unambiguous lone PC).
    resolved = _resolved_events(captured)
    assert len(resolved) == 1
    rf = resolved[0]["fields"]
    assert rf["recipient"] == "Rux"
    assert rf["resolution_mode"] == "tagged"
    assert rf["lane"] == "gained"


# --------------------------------------------------------------------------
# 8: wiring — resolver reachable from the real apply path, per-item event
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_helper_wired_into_real_apply_path_emits_per_item_recipient_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAUDE.md wiring mandate: the resolver is invoked from the real
    ``_apply_narration_result_to_snapshot`` seam (the one
    ``_execute_narration_turn`` calls), not merely unit-callable. A
    tagged 2-seat round must surface a per-applied-item
    ``item_recipient_resolved`` watcher (component=inventory) naming the
    resolved seated PC + lane + mode."""
    captured = await _capture(monkeypatch, "adr108-wiring")
    snap = _two_seat_snapshot()

    result = NarrationTurnResult(
        narration="The dockmaster slides the scan chip to Catalina.",
        items_gained=[
            {
                "name": "Corvette Scan Data",
                "category": "quest",
                "recipient": "Catalina Valentine",
            }
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="p1",
        room=room_for(snap),
        acting_character_name="Ritali Veer",
    )
    await asyncio.sleep(0.05)

    resolved = _resolved_events(captured)
    assert len(resolved) == 1, (
        "expected exactly one item_recipient_resolved watcher from the "
        f"real apply path (got {len(resolved)})"
    )
    f = resolved[0]["fields"]
    assert f["field"] == "inventory"
    assert f["recipient"] == "Catalina Valentine"
    assert f["resolution_mode"] == "tagged"
    assert f["lane"] == "gained"
    assert f["item"] == "Corvette Scan Data"
    assert resolved[0]["component"] == "inventory"
