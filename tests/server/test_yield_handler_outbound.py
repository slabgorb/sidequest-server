"""Regression tests for playtest 2026-04-30 — Confrontation Yield
button is a silent no-op.

Pre-fix flow:
1. Client clicks Yield → sends YIELD message.
2. ``YieldHandler`` calls ``handle_yield`` which mutates the snapshot
   (``actor.withdrawn=True``, sets ``pending_resolution_signal``,
   refunds edge) and publishes watcher-hub events.
3. Handler returns ``[]`` — no outbound message to the client.
4. Watcher-hub events go to the GM dashboard, not the player socket.
   ``_watcher_publish`` does NOT log to the standard logger, so
   ``grep -i yield /tmp/sidequest-server.log`` returns nothing.
5. Player perceives a silent no-op: edge bars unchanged in the UI,
   action menu still rendered, no error toast, no console.warn.

The encounter HAS resolved server-side (for solo PCs) — the snapshot
is correct, ``pending_resolution_signal`` is queued. But the UI has
no way to know without an outbound message.

Fix:
- Log INFO at handler entry, on resolution, and on rejection so
  ``grep yield /tmp/sidequest-server.log`` produces the expected trail.
- Build a fresh CONFRONTATION payload from the post-yield encounter
  state and return it. ``active=False`` when the encounter is
  fully resolved (overlay unmounts); ``active=True`` with the updated
  actors list when the yield is partial (multi-actor party).
- The narration of the outcome still flows through the next narrator
  turn via ``pending_resolution_signal`` — that's the existing spec
  contract from 2026-04-25-dual-track-momentum-design.md §Yield action.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    StructuredEncounter,
)
from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import YieldMessage
from sidequest.server.session_handler import _State


def _make_session_with_yield(snapshot, pack):
    """Build a session-like object the YieldHandler needs.

    The handler reads ``session._state``, ``session._session_data``,
    plus a few fields on session_data. Mock the minimum surface so
    we exercise the real handler logic without spinning a WebSocket.
    """
    session = MagicMock()
    session._state = _State.Playing

    sd = MagicMock()
    sd.snapshot = snapshot
    sd.player_id = "p1"
    sd.player_name = "Sam"
    sd.genre_slug = "test_pack"
    sd.genre_pack = pack

    session._session_data = sd
    return session


def _solo_combat_encounter():
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(
            name="momentum", current=4, starting=0, threshold=10,
        ),
        opponent_metric=EncounterMetric(
            name="momentum", current=7, starting=0, threshold=10,
        ),
        actors=[
            EncounterActor(name="Sam", role="combatant", side="player"),
            EncounterActor(name="Promo", role="combatant", side="opponent"),
        ],
    )


def _multi_pc_encounter():
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(
            name="momentum", current=4, starting=0, threshold=10,
        ),
        opponent_metric=EncounterMetric(
            name="momentum", current=7, starting=0, threshold=10,
        ),
        actors=[
            EncounterActor(name="Sam", role="combatant", side="player"),
            EncounterActor(name="Eli", role="combatant", side="player"),
            EncounterActor(name="Promo", role="combatant", side="opponent"),
        ],
    )


def _character(name: str = "Sam") -> Character:
    core = CreatureCore(
        name=name,
        description="a survivor",
        personality="gritty",
        inventory=Inventory(),
    )
    return Character(
        core=core, char_class="Fighter", race="Human",
        backstory="A wandering fighter",
    )


def _make_yield_msg() -> YieldMessage:
    return YieldMessage(
        type=MessageType.YIELD,
        payload={},
        player_id="p1",
    )


@pytest.mark.asyncio
async def test_yield_solo_pc_emits_clear_confrontation_outbound(
    snapshot_with_pack, character_named_sam, caplog,
):
    """The Parsley/Sage repro shape: solo PC clicks Yield, encounter
    resolves immediately. Handler must emit a CONFRONTATION message
    with ``active=False`` so the overlay unmounts client-side.

    Pre-fix this returned ``[]`` and the UI showed stale state.
    """
    from sidequest.handlers.yield_action import HANDLER

    snap, pack = snapshot_with_pack
    snap.encounter = _solo_combat_encounter()
    snap.characters.append(character_named_sam)
    session = _make_session_with_yield(snap, pack)

    with caplog.at_level("INFO"):
        outbound = await HANDLER.handle(session, _make_yield_msg())

    # Encounter resolved → overlay must unmount.
    assert len(outbound) == 1, (
        f"YieldHandler must emit a CONFRONTATION update so the UI "
        f"reflects the resolution. Pre-fix returned []. Got: {outbound!r}"
    )
    msg = outbound[0]
    assert msg.type == MessageType.CONFRONTATION
    assert msg.payload.active is False, (
        "encounter resolved → overlay payload.active=False so UI unmounts"
    )
    # Snapshot side-effects from handle_yield still hold.
    assert snap.encounter.resolved is True
    assert snap.encounter.outcome == "yielded"

    # Lie-detector trail: grep -i yield /tmp/sidequest-server.log must
    # find SOMETHING. Log lines from the handler itself satisfy this
    # without depending on watcher-hub-to-OTLP wiring.
    assert any(
        "session.yield_received" in r.message for r in caplog.records
    ), "missing INFO log on yield receipt"
    assert any(
        "session.yield_resolved" in r.message for r in caplog.records
    ), "missing INFO log on yield resolution"


@pytest.mark.asyncio
async def test_yield_multi_pc_partial_emits_active_confrontation(
    snapshot_with_pack,
):
    """Multi-PC party where only one PC yields — encounter remains
    active until every player-side actor withdraws. Handler must
    emit a CONFRONTATION with ``active=True`` and the updated actors
    list (Sam.withdrawn=True) so the UI mirrors the partial yield.
    """
    from sidequest.handlers.yield_action import HANDLER

    snap, pack = snapshot_with_pack
    snap.encounter = _multi_pc_encounter()
    snap.characters.extend([_character("Sam"), _character("Eli")])
    session = _make_session_with_yield(snap, pack)

    outbound = await HANDLER.handle(session, _make_yield_msg())

    assert len(outbound) == 1
    msg = outbound[0]
    assert msg.type == MessageType.CONFRONTATION
    assert msg.payload.active is True, (
        "partial yield (Eli still active) → overlay stays mounted"
    )
    sam_actor = next(a for a in msg.payload.actors if a["name"] == "Sam")
    assert sam_actor["withdrawn"] is True, (
        "Sam's withdrawal must be reflected in the outbound payload "
        "so the UI updates the actor's portrait/state"
    )
    eli_actor = next(a for a in msg.payload.actors if a["name"] == "Eli")
    assert eli_actor["withdrawn"] is False
    assert snap.encounter.resolved is False


@pytest.mark.asyncio
async def test_yield_no_active_encounter_returns_error_message(
    snapshot_with_pack, caplog,
):
    """Defensive: pressing Yield with no active encounter (edge case
    if the UI desyncs) must produce an ERROR, not a silent no-op.
    """
    from sidequest.handlers.yield_action import HANDLER

    snap, pack = snapshot_with_pack
    # snap.encounter = None  (default — no encounter active)
    session = _make_session_with_yield(snap, pack)

    with caplog.at_level("WARNING"):
        outbound = await HANDLER.handle(session, _make_yield_msg())

    assert len(outbound) == 1
    msg = outbound[0]
    assert msg.type == "ERROR"
    assert any(
        "session.yield_rejected" in r.message for r in caplog.records
    ), "missing WARNING log on yield rejection"


@pytest.mark.asyncio
async def test_yield_logs_receipt_for_grep(snapshot_with_pack, character_named_sam, caplog):
    """Lie-detector smoke test: ``grep -i yield`` against the server
    log must return at least one line per yield, even on the partial-
    yield branch where no resolution log fires. Pre-fix the only
    log surface was watcher_hub which doesn't log to stdout.
    """
    from sidequest.handlers.yield_action import HANDLER

    snap, pack = snapshot_with_pack
    snap.encounter = _solo_combat_encounter()
    snap.characters.append(character_named_sam)
    session = _make_session_with_yield(snap, pack)

    with caplog.at_level("INFO", logger="sidequest.handlers.yield_action"):
        await HANDLER.handle(session, _make_yield_msg())

    yield_logs = [r for r in caplog.records if "yield" in r.message.lower()]
    assert len(yield_logs) >= 2, (
        f"expected ≥2 yield-tagged log lines (received + resolved), got "
        f"{len(yield_logs)}: {[r.message for r in yield_logs]}"
    )


@pytest.mark.asyncio
async def test_yield_state_check_unchanged_for_non_playing():
    """Pre-existing guard: yielding outside _State.Playing returns
    an ERROR. Verify the new outbound logic didn't regress this.
    """
    from sidequest.handlers.yield_action import HANDLER

    session = MagicMock()
    session._state = _State.Creating
    session._session_data = MagicMock()

    outbound = await HANDLER.handle(session, _make_yield_msg())

    assert len(outbound) == 1
    assert outbound[0].type == "ERROR"
