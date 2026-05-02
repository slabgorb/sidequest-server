"""End-to-end resolution path: encounter resolves → CONFRONTATION_OUTCOME stashed
+ pending_status_promotions drained into Character.core.statuses — Story 47-3.

Westley (Reviewer) flagged the outcome path as half-wired in the first
review pass: ``resolve_magic_confrontation`` returned a payload but
nothing stashed it for the WS dispatcher, and ``pending_status_promotions``
had no drainer (so AC5's "Status list updated" promise was unmet). These
tests pin the wired contract: after ``_resolve_magic_confrontation_if_applicable``
runs, the snapshot carries the outbound payload AND the actor's
Character has the new Status.
"""

from __future__ import annotations

import pytest

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore
from sidequest.game.session import GameSnapshot
from sidequest.game.status import StatusSeverity
from sidequest.magic.confrontations import ConfrontationDefinition
from sidequest.magic.models import WorldMagicConfig
from sidequest.magic.state import MagicState


def _make_character(name: str) -> Character:
    """Build a Character with minimum-viable required fields for tests."""
    return Character.model_construct(
        core=CreatureCore.model_construct(name=name, statuses=[]),
        backstory="",
        narrative_state="",
        hooks=[],
        char_class="",
        race="",
        pronouns="",
        stats={},
        abilities=[],
        known_facts=[],
        is_friendly=True,
    )


def _the_bleeding_through() -> ConfrontationDefinition:
    return ConfrontationDefinition(
        id="the_bleeding_through",
        label="The Bleeding-Through",
        plugin_tie_ins=["innate_v1"],
        auto_fire=True,
        auto_fire_trigger="sanity <= 0.40",
        rounds=1,
        resource_pool={"primary": "sanity", "secondary": "vitality"},
        description="x",
        outcomes={
            "clear_win": {"mandatory_outputs": ["control_tier_advance"]},
            "pyrrhic_win": {
                "mandatory_outputs": ["control_tier_advance", "status_add_scar"],
            },
            "clear_loss": {"mandatory_outputs": ["status_add_scar"]},
            "refused": {"mandatory_outputs": ["sanity_decrement"]},
        },
    )


def _make_snapshot(world_config: WorldMagicConfig, *, with_character: bool = True) -> GameSnapshot:
    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    state.confrontations = [_the_bleeding_through()]
    snapshot = GameSnapshot.model_construct(magic_state=state)
    if with_character:
        # Real Character on the snapshot so the drainer can find an actor.
        snapshot.characters = [_make_character("sira_mendes")]
    return snapshot


def test_resolution_stashes_outcome_payload_on_snapshot(
    world_config: WorldMagicConfig,
) -> None:
    """``_resolve_magic_confrontation_if_applicable`` populates ``pending_magic_confrontation_outcome``."""
    from sidequest.server.narration_apply import (
        _resolve_magic_confrontation_if_applicable,
    )

    snapshot = _make_snapshot(world_config)
    assert snapshot.pending_magic_confrontation_outcome is None

    _resolve_magic_confrontation_if_applicable(
        snapshot=snapshot,
        encounter_type="the_bleeding_through",
        outcome="pyrrhic_win",
        actor="sira_mendes",
    )

    payload = snapshot.pending_magic_confrontation_outcome
    assert payload is not None
    assert payload["confrontation_id"] == "the_bleeding_through"
    assert payload["label"] == "The Bleeding-Through"
    assert payload["branch"] == "pyrrhic_win"
    # pyrrhic_win mandatory_outputs include control_tier_advance + status_add_scar
    assert "control_tier_advance" in payload["mandatory_outputs"]
    assert "status_add_scar" in payload["mandatory_outputs"]


def test_resolution_drains_status_promotions_to_character(
    world_config: WorldMagicConfig,
) -> None:
    """The drainer moves queued promotions into ``Character.core.statuses`` at outcome time."""
    from sidequest.server.narration_apply import (
        _resolve_magic_confrontation_if_applicable,
    )

    snapshot = _make_snapshot(world_config)
    assert snapshot.characters[0].core.statuses == []

    # pyrrhic_win on the_bleeding_through includes status_add_scar — the
    # output queues a Scar promotion onto magic_state.pending_status_promotions
    # via the apply_mandatory_outputs handler.
    _resolve_magic_confrontation_if_applicable(
        snapshot=snapshot,
        encounter_type="the_bleeding_through",
        outcome="pyrrhic_win",
        actor="sira_mendes",
    )

    # Drainer ran inline: queue should be empty, character should have
    # the new Scar status.
    assert snapshot.magic_state.pending_status_promotions == []
    statuses = snapshot.characters[0].core.statuses
    assert len(statuses) == 1
    assert statuses[0].severity == StatusSeverity.Scar


def test_resolution_orphan_promotion_when_actor_missing(
    world_config: WorldMagicConfig,
) -> None:
    """Promotions for unknown actors stay queued and emit a warning span."""
    from sidequest.server.narration_apply import (
        _resolve_magic_confrontation_if_applicable,
    )

    # Snapshot has the magic state + confrontation but NO Character roster
    # entry for "sira_mendes" — the drainer cannot match the queued
    # promotion to a target.
    snapshot = _make_snapshot(world_config, with_character=False)

    _resolve_magic_confrontation_if_applicable(
        snapshot=snapshot,
        encounter_type="the_bleeding_through",
        outcome="pyrrhic_win",
        actor="sira_mendes",
    )

    # Queue retains the orphaned promotion; no Status was injected
    # anywhere (snapshot.characters is empty).
    queued = snapshot.magic_state.pending_status_promotions
    assert len(queued) == 1
    assert queued[0]["actor"] == "sira_mendes"
    assert queued[0]["severity"] == "Scar"
    assert snapshot.characters == []


def test_resolution_unmapped_outcome_does_not_stash_payload(
    world_config: WorldMagicConfig,
) -> None:
    """Unknown ``outcome`` strings (e.g. 'unknown') log + return without stashing."""
    from sidequest.server.narration_apply import (
        _resolve_magic_confrontation_if_applicable,
    )

    snapshot = _make_snapshot(world_config)

    _resolve_magic_confrontation_if_applicable(
        snapshot=snapshot,
        encounter_type="the_bleeding_through",
        outcome="garbled_outcome_string",
        actor="sira_mendes",
    )

    assert snapshot.pending_magic_confrontation_outcome is None
    assert snapshot.characters[0].core.statuses == []


def test_resolution_non_magic_encounter_passes_through(
    world_config: WorldMagicConfig,
) -> None:
    """Non-magic encounter types (not in ``MagicState.confrontations``) are no-op."""
    from sidequest.server.narration_apply import (
        _resolve_magic_confrontation_if_applicable,
    )

    snapshot = _make_snapshot(world_config)

    _resolve_magic_confrontation_if_applicable(
        snapshot=snapshot,
        encounter_type="some_other_encounter",
        outcome="clear_win",
        actor="sira_mendes",
    )

    assert snapshot.pending_magic_confrontation_outcome is None


def test_resolution_no_magic_state_returns_silently(
    world_config: WorldMagicConfig,  # noqa: ARG001 — fixture only used for signature parity
) -> None:
    """When ``magic_state`` is None, the function returns without raising."""
    from sidequest.server.narration_apply import (
        _resolve_magic_confrontation_if_applicable,
    )

    snapshot = GameSnapshot.model_construct(magic_state=None)

    _resolve_magic_confrontation_if_applicable(
        snapshot=snapshot,
        encounter_type="the_bleeding_through",
        outcome="clear_win",
        actor="sira_mendes",
    )

    assert snapshot.pending_magic_confrontation_outcome is None


def test_drainer_skips_promotions_with_invalid_severity(
    world_config: WorldMagicConfig,
) -> None:
    """Malformed severity strings stay queued + warn — they do not crash the drainer."""
    from sidequest.server.narration_apply import _drain_pending_status_promotions

    snapshot = _make_snapshot(world_config)
    snapshot.magic_state.pending_status_promotions.append(
        {"actor": "sira_mendes", "text": "Bad severity", "severity": "NotARealSeverity"}
    )

    _drain_pending_status_promotions(snapshot=snapshot)

    # The bad entry stayed queued; the valid actor's character has no
    # status (we never injected a queue entry the drainer could process).
    assert len(snapshot.magic_state.pending_status_promotions) == 1
    assert snapshot.characters[0].core.statuses == []


@pytest.fixture
def coyote_world_config(world_config: WorldMagicConfig) -> WorldMagicConfig:
    return world_config
