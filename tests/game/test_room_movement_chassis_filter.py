"""S1 step 3 — process_room_entry reads magic_state.confrontations.

The reader filters to chassis-coupled entries (register == "intimate")
before calling find_eligible_room_autofire. World-scoped magic
confrontations (register != "intimate") MUST NOT be considered for the
rig-coupled room-entry auto-fire path — they're driven by the bar-DSL
threshold evaluator, not by room entry."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SPACE_OPERA = REPO_ROOT / "sidequest-content" / "genre_packs" / "space_opera"


def _make_world_magic_config():
    """Build a minimum-valid WorldMagicConfig for coyote_star.

    Plan deviation 2026-05-04 (TEA): the plan's snippet
    ``WorldMagicConfig(world_slug="coyote_star", ledger_bars=[])`` is
    missing fields that are now required (genre_slug, allowed_sources,
    active_plugins, intensity, world_knowledge, visibility, hard_limits,
    cost_types, narrator_register). Mirror the canonical fixture from
    tests/magic/conftest.py with empties where allowed."""
    from sidequest.magic.models import WorldKnowledge, WorldMagicConfig

    return WorldMagicConfig(
        world_slug="coyote_star",
        genre_slug="space_opera",
        allowed_sources=[],
        active_plugins=[],
        intensity=0.0,
        world_knowledge=WorldKnowledge(primary="classified", local_register="folkloric"),
        visibility={"primary": "feared", "local_register": "dismissed"},
        hard_limits=[],
        cost_types=[],
        ledger_bars=[],
        narrator_register="test",
    )


def _bootstrap_coyote_star_snapshot():
    """Build a snapshot with chassis_registry + magic_state both populated
    from the live space_opera/coyote_star fixture."""
    from sidequest.game.chassis import init_chassis_registry
    from sidequest.game.session import GameSnapshot
    from sidequest.genre.loader import load_genre_pack
    from sidequest.magic.state import MagicState

    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="Galley",
    )
    snap.magic_state = MagicState.from_config(_make_world_magic_config())
    init_chassis_registry(snap, pack)
    return snap


def test_process_room_entry_passes_magic_state_confrontations_to_finder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader must source its confrontation list from
    magic_state.confrontations (filtered to chassis-coupled), not from the
    legacy world_confrontations field."""
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")

    snap = _bootstrap_coyote_star_snapshot()
    chassis = next(iter(snap.chassis_registry.values()))
    char_id = chassis.bond_ledger[0].character_id

    captured: dict[str, object] = {}

    def _spy_find(*, confrontations, **kwargs):
        captured["confrontations"] = list(confrontations)
        return []  # nothing eligible — we only care about what was passed in

    import sidequest.game.room_movement as rm

    monkeypatch.setattr(rm, "find_eligible_room_autofire", _spy_find)

    rm.process_room_entry(snap, character_id=char_id, room_id="Galley", current_turn=1)

    received = captured["confrontations"]
    received_ids = {c.id for c in received}
    magic_state_ids = {c.id for c in snap.magic_state.confrontations if c.register == "intimate"}
    # Every intimate confrontation on magic_state was passed; nothing else.
    assert received_ids == magic_state_ids
    # And the_tea_brew (the canonical coyote_star intimate confrontation)
    # is in the set.
    assert "the_tea_brew" in received_ids


def test_process_room_entry_excludes_non_intimate_confrontations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confrontations with register != 'intimate' must NOT reach the
    find_eligible_room_autofire call — they're bar-DSL, not rig-coupled."""
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")
    from sidequest.magic.confrontations import (
        ConfrontationBranch,
        ConfrontationDefinition,
    )

    snap = _bootstrap_coyote_star_snapshot()
    chassis = next(iter(snap.chassis_registry.values()))
    char_id = chassis.bond_ledger[0].character_id

    # Inject a world-scoped (bar-DSL) confrontation. It must NOT be passed
    # to find_eligible_room_autofire by process_room_entry.
    snap.magic_state.confrontations.append(
        ConfrontationDefinition(
            id="the_bleeding_through",
            label="The Bleeding Through",
            plugin_tie_ins=[],
            register=None,  # bar-DSL — no chassis register
            rounds=3,
            resource_pool={},
            description="A test bar-DSL confrontation.",
            outcomes={
                "clear_win": ConfrontationBranch(mandatory_outputs=["sanity_increment"]),
                "pyrrhic_win": ConfrontationBranch(mandatory_outputs=["sanity_increment"]),
                "clear_loss": ConfrontationBranch(mandatory_outputs=["sanity_decrement"]),
                "refused": ConfrontationBranch(mandatory_outputs=["sanity_decrement"]),
            },
        )
    )

    captured: dict[str, object] = {}

    def _spy_find(*, confrontations, **kwargs):
        captured["confrontations"] = list(confrontations)
        return []

    import sidequest.game.room_movement as rm

    monkeypatch.setattr(rm, "find_eligible_room_autofire", _spy_find)

    rm.process_room_entry(snap, character_id=char_id, room_id="Galley", current_turn=1)

    received_ids = {c.id for c in captured["confrontations"]}
    assert "the_bleeding_through" not in received_ids
    assert "the_tea_brew" in received_ids
