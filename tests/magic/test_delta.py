"""StateDelta.magic flag and protocol propagation.

Task 2.4: verify that:
  1. Internal StateDelta has a magic: bool field (defaults False).
  2. compute_delta sets magic=True when MagicState serialization changes.
  3. compute_delta leaves magic=False when MagicState is identical.
  4. Protocol StateDelta carries magic_state: dict | None.
"""
from __future__ import annotations

from sidequest.game.delta import StateDelta, compute_delta, snapshot
from sidequest.magic.models import (
    HardLimit,
    LedgerBarSpec,
    WorldKnowledge,
    WorldMagicConfig,
)
from sidequest.magic.state import BarKey, MagicState


def _make_world_config() -> WorldMagicConfig:
    """Minimal WorldMagicConfig sufficient for delta tests."""
    return WorldMagicConfig(
        world_slug="coyote_reach",
        genre_slug="space_opera",
        allowed_sources=["innate"],
        active_plugins=["innate_v1"],
        intensity=0.25,
        world_knowledge=WorldKnowledge(primary="classified", local_register="folkloric"),
        visibility={"primary": "feared"},
        hard_limits=[HardLimit(id="psionics_never_decisive", description="x")],
        cost_types=["sanity"],
        ledger_bars=[
            LedgerBarSpec(
                id="sanity",
                scope="character",
                direction="down",
                range=(0.0, 1.0),
                threshold_low=0.40,
                consequence_on_low_cross="auto-fire The Bleeding-Through",
                starts_at_chargen=1.0,
            ),
        ],
        narrator_register="x",
    )


def test_state_delta_has_magic_flag():
    """Internal StateDelta must carry magic: bool defaulting to False."""
    d = StateDelta()
    assert d.magic is False


def test_compute_delta_sets_magic_flag_when_state_changes():
    """compute_delta() sets magic=True when MagicState.ledger changes."""
    from sidequest.game.session import GameSnapshot

    config = _make_world_config()
    state_a = MagicState.from_config(config)
    state_a.add_character("sira_mendes")

    state_b = MagicState.from_config(config)
    state_b.add_character("sira_mendes")
    state_b.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.66
    )

    snap_a = GameSnapshot(magic_state=state_a)
    snap_b = GameSnapshot(magic_state=state_b)

    before = snapshot(snap_a)
    after = snapshot(snap_b)
    delta = compute_delta(before, after)
    assert delta.magic is True


def test_compute_delta_magic_flag_false_when_unchanged():
    """compute_delta() leaves magic=False when MagicState is bit-identical."""
    from sidequest.game.session import GameSnapshot

    config = _make_world_config()
    state_a = MagicState.from_config(config)
    state_a.add_character("sira_mendes")
    state_b = state_a.model_copy(deep=True)

    snap_a = GameSnapshot(magic_state=state_a)
    snap_b = GameSnapshot(magic_state=state_b)

    before = snapshot(snap_a)
    after = snapshot(snap_b)
    delta = compute_delta(before, after)
    assert delta.magic is False


def test_protocol_state_delta_has_magic_state_field():
    """Protocol StateDelta must expose magic_state: dict | None."""
    from sidequest.protocol.models import StateDelta as ProtocolStateDelta

    assert "magic_state" in ProtocolStateDelta.model_fields
    # Field must default to None (opaque dict — client deserializes via TS types).
    proto = ProtocolStateDelta()
    assert proto.magic_state is None


def test_shared_world_delta_to_state_delta_carries_magic_state():
    """Phase 4 wire path: the projection helper must accept a magic_state
    payload and embed it on the returned wire StateDelta. Without this,
    NARRATION_END never carries magic_state to the UI and bars never
    animate even though server-side mutations land correctly.
    """
    from sidequest.game.shared_world_delta import SharedWorldDelta
    from sidequest.server.session_handler import (
        _shared_world_delta_to_state_delta,
    )

    config = _make_world_config()
    state = MagicState.from_config(config)
    state.add_character("sira_mendes")

    delta = SharedWorldDelta(location="rusted_atrium", encounter_id=None, party_formation=[])
    wire = _shared_world_delta_to_state_delta(
        delta,
        magic_state=state.model_dump(mode="json"),
    )

    assert wire.magic_state is not None, (
        "wire StateDelta.magic_state must carry the dict payload "
        "when the projection is given a non-None magic_state arg"
    )
    # Sanity: the dict round-trips the bar registry the UI needs.
    assert "ledger" in wire.magic_state
    assert "config" in wire.magic_state


def test_shared_world_delta_to_state_delta_magic_state_optional():
    """Default kwarg keeps existing call sites working — wire magic_state
    stays None when the helper is called without the new arg.
    """
    from sidequest.game.shared_world_delta import SharedWorldDelta
    from sidequest.server.session_handler import (
        _shared_world_delta_to_state_delta,
    )

    delta = SharedWorldDelta(location="rusted_atrium", encounter_id=None, party_formation=[])
    wire = _shared_world_delta_to_state_delta(delta)
    assert wire.magic_state is None


def test_websocket_session_handler_passes_magic_state_to_state_delta():
    """Wire-first source grep: the NARRATION_END emit site must thread
    snapshot.magic_state into the projection helper. Without this, the
    helper's new kwarg is dead and the UI never receives bars.
    """
    from sidequest.server import websocket_session_handler

    with open(websocket_session_handler.__file__) as fh:
        source = fh.read()
    assert "magic_state=" in source, (
        "websocket_session_handler.py must pass magic_state= into "
        "_shared_world_delta_to_state_delta — wire-first gate fails."
    )
