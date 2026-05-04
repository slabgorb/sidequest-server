"""S5 — transient magic queues are not serialized.

These three fields exist for in-memory handler logic but must NEVER appear
in the persisted JSON. A save mid-handler should round-trip with empty
queues (which is correct: queues are derivable from snapshot state on the
next narration turn)."""

from __future__ import annotations

import json

from sidequest.game.session import GameSnapshot


def test_pending_magic_auto_fires_excluded_from_dump() -> None:
    snap = GameSnapshot(genre_slug="g", world_slug="w")
    snap.pending_magic_auto_fires.append({"confrontation_id": "x"})

    dumped = snap.model_dump_json()
    parsed = json.loads(dumped)

    assert "pending_magic_auto_fires" not in parsed


def test_pending_magic_confrontation_outcome_excluded_from_dump() -> None:
    snap = GameSnapshot(genre_slug="g", world_slug="w")
    snap.pending_magic_confrontation_outcome = {"branch": "clear_win"}

    dumped = snap.model_dump_json()
    parsed = json.loads(dumped)

    assert "pending_magic_confrontation_outcome" not in parsed


def test_pending_status_promotions_excluded_from_dump() -> None:
    """MagicState.pending_status_promotions is also excluded — it lives on
    MagicState rather than directly on the snapshot, but the persistence
    boundary is still ``GameSnapshot.model_dump_json``."""
    from sidequest.magic.models import WorldMagicConfig
    from sidequest.magic.state import MagicState

    config = WorldMagicConfig(world_slug="w", ledger_bars=[])
    state = MagicState.from_config(config)
    state.pending_status_promotions.append({"actor": "a", "text": "Bleeding", "severity": "Wound"})

    snap = GameSnapshot(genre_slug="g", world_slug="w", magic_state=state)
    dumped = snap.model_dump_json()
    parsed = json.loads(dumped)

    # MagicState appears, but its pending_status_promotions does not.
    assert parsed.get("magic_state") is not None
    assert "pending_status_promotions" not in parsed["magic_state"]


def test_load_after_dump_reinitializes_queues_empty() -> None:
    """Round-trip: queues populate, dump excludes them, reload gives empty queues."""
    snap = GameSnapshot(genre_slug="g", world_slug="w")
    snap.pending_magic_auto_fires.append({"confrontation_id": "x"})

    reloaded = GameSnapshot.model_validate_json(snap.model_dump_json())

    assert reloaded.pending_magic_auto_fires == []
    assert reloaded.pending_magic_confrontation_outcome is None
