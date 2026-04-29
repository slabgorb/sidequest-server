"""End-to-end: narrator emits magic_working → server applies."""
from __future__ import annotations

import pytest

from sidequest.magic.models import (
    HardLimit,
    LedgerBarSpec,
    WorldKnowledge,
    WorldMagicConfig,
)


@pytest.fixture()
def world_config() -> WorldMagicConfig:
    """Local override of the conftest world_config.

    Adds ``no_resurrection`` to ``hard_limits`` so the validator's keyword
    detector (id → "resurrection") matches the test 2 narrator_basis
    "resurrection of the dead pilot via psychic touch". The conftest
    fixture only ships ``psionics_never_decisive``, which would not match
    that basis. Per the conftest comment "pytest local wins", this
    file-scoped fixture replaces the session/conftest fixture for tests
    in this module.
    """
    return WorldMagicConfig(
        world_slug="coyote_reach",
        genre_slug="space_opera",
        allowed_sources=["innate", "item_based"],
        active_plugins=["innate_v1", "item_legacy_v1"],
        intensity=0.25,
        world_knowledge=WorldKnowledge(
            primary="classified", local_register="folkloric"
        ),
        visibility={"primary": "feared", "local_register": "dismissed"},
        hard_limits=[
            HardLimit(id="no_resurrection", description="death is permanent"),
            HardLimit(
                id="psionics_never_decisive",
                description="psionics cannot resolve a confrontation alone",
            ),
        ],
        cost_types=["sanity", "notice", "vitality"],
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
            LedgerBarSpec(
                id="notice",
                scope="character",
                direction="up",
                range=(0.0, 1.0),
                threshold_high=0.75,
                consequence_on_high_cross="auto-fire The Quiet Word",
                starts_at_chargen=0.0,
            ),
            LedgerBarSpec(
                id="hegemony_heat",
                scope="world",
                direction="up",
                range=(0.0, 1.0),
                threshold_high=0.70,
                consequence_on_high_cross="escalation",
                decay_per_session=0.05,
                starts_at_chargen=0.30,
            ),
        ],
        narrator_register="x",
    )


@pytest.fixture
def coyote_snapshot(world_config):
    from sidequest.game.session import GameSnapshot
    from sidequest.magic.state import MagicState

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    return GameSnapshot.model_construct(magic_state=state)


def test_apply_magic_working_clean_pass(coyote_snapshot):
    """Clean working: ledger updates, no flags."""
    from sidequest.magic.state import BarKey
    from sidequest.server.narration_apply import apply_magic_working

    patch_field = {
        "plugin": "innate_v1",
        "mechanism": "condition",
        "actor": "sira_mendes",
        "costs": {"sanity": 0.12},
        "domain": "psychic",
        "narrator_basis": "alien-tech proximity triggers reflexive sympathetic-feel",
        "flavor": "acquired",
        "consent_state": "involuntary",
    }
    result = apply_magic_working(snapshot=coyote_snapshot, patch_field=patch_field)

    assert result.flags == []
    sanity = coyote_snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    )
    assert sanity.value == pytest.approx(0.88)


def test_apply_magic_working_deep_red_flagged(coyote_snapshot):
    """Hard-limit violation: ledger still updates but result.flags carries DEEP_RED."""
    from sidequest.magic.models import FlagSeverity
    from sidequest.server.narration_apply import apply_magic_working

    patch_field = {
        "plugin": "innate_v1",
        "mechanism": "condition",
        "actor": "sira_mendes",
        "costs": {"sanity": 0.12},
        "domain": "psychic",
        "narrator_basis": "resurrection of the dead pilot via psychic touch",
        "flavor": "acquired",
        "consent_state": "involuntary",
    }
    result = apply_magic_working(snapshot=coyote_snapshot, patch_field=patch_field)

    assert any(f.severity == FlagSeverity.DEEP_RED for f in result.flags)


def test_apply_magic_working_malformed_patch_raises_parse_error(coyote_snapshot):
    from sidequest.server.narration_apply import (
        MagicWorkingParseError,
        apply_magic_working,
    )

    patch_field = {"plugin": "innate_v1"}  # missing required fields
    with pytest.raises(MagicWorkingParseError):
        apply_magic_working(snapshot=coyote_snapshot, patch_field=patch_field)


def test_apply_magic_working_returns_threshold_crossings(coyote_snapshot):
    from sidequest.magic.state import BarKey
    from sidequest.server.narration_apply import apply_magic_working

    # Pre-set sanity to 0.45 so a 0.10 cost crosses 0.40
    coyote_snapshot.magic_state.set_bar_value(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.45
    )

    patch_field = {
        "plugin": "innate_v1",
        "mechanism": "condition",
        "actor": "sira_mendes",
        "costs": {"sanity": 0.10},
        "domain": "psychic",
        "narrator_basis": "x",
        "flavor": "acquired",
        "consent_state": "involuntary",
    }
    result = apply_magic_working(snapshot=coyote_snapshot, patch_field=patch_field)

    assert len(result.crossings) == 1
    assert "Bleeding-Through" in result.crossings[0].consequence


def test_apply_magic_working_no_magic_state_raises_parse_error():
    """Snapshot without magic_state must fail loudly per CLAUDE.md no-silent-fallback."""
    from sidequest.game.session import GameSnapshot
    from sidequest.server.narration_apply import (
        MagicWorkingParseError,
        apply_magic_working,
    )

    snapshot = GameSnapshot.model_construct(magic_state=None)
    patch_field = {
        "plugin": "innate_v1",
        "mechanism": "condition",
        "actor": "sira_mendes",
        "costs": {"sanity": 0.10},
        "domain": "psychic",
        "narrator_basis": "x",
        "flavor": "acquired",
        "consent_state": "involuntary",
    }
    with pytest.raises(MagicWorkingParseError):
        apply_magic_working(snapshot=snapshot, patch_field=patch_field)


def test_narration_apply_pipeline_invokes_apply_magic_working(coyote_snapshot):
    """Wiring test (CLAUDE.md): the apply pipeline must actually call
    apply_magic_working when the narrator emitted a magic_working field.

    Without this test, the branch in _apply_narration_result_to_snapshot
    could rot (imported but never reached), which is exactly the failure
    mode CLAUDE.md "Verify Wiring, Not Just Existence" warns against.
    """
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.game.turn import TurnManager
    from sidequest.magic.state import BarKey
    from sidequest.server.narration_apply import (
        _apply_narration_result_to_snapshot,
    )

    # Minimum required GameSnapshot fields the apply pipeline reads:
    # turn_manager.interaction, location, discovered_regions, etc.
    # We mutate the existing coyote_snapshot to satisfy them rather than
    # building from scratch; magic_state is already populated.
    coyote_snapshot.turn_manager = TurnManager()
    coyote_snapshot.location = ""
    coyote_snapshot.discovered_regions = []
    coyote_snapshot.quest_log = {}
    coyote_snapshot.lore_established = []
    coyote_snapshot.npc_registry = []
    coyote_snapshot.characters = []
    coyote_snapshot.encounter = None

    result = NarrationTurnResult(
        narration="some prose",
        magic_working={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.05},
            "domain": "psychic",
            "narrator_basis": "x",
            "flavor": "acquired",
            "consent_state": "involuntary",
        },
    )

    _apply_narration_result_to_snapshot(
        coyote_snapshot, result, player_name="Sira"
    )

    # Sanity dropped by 0.05 from 1.00 → 0.95: proves apply_magic_working ran.
    sanity = coyote_snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    )
    assert sanity.value == pytest.approx(0.95)
