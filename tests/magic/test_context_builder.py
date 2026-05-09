# sidequest-server/tests/magic/test_context_builder.py
from __future__ import annotations

import pytest

from sidequest.magic.context_builder import build_magic_context_block
from sidequest.magic.state import BarKey, MagicState


@pytest.fixture
def world_state(world_config):
    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    state.set_bar_value(BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity"), 0.78)
    state.set_bar_value(BarKey(scope="character", owner_id="sira_mendes", bar_id="notice"), 0.22)
    return state


def test_block_is_empty_string_when_state_is_none():
    assert build_magic_context_block(magic_state=None, actor_id="sira_mendes") == ""


def test_block_lists_allowed_sources(world_state):
    block = build_magic_context_block(magic_state=world_state, actor_id="sira_mendes")
    assert "allowed_sources" in block
    assert "innate" in block
    assert "item_based" in block


def test_block_lists_hard_limits(world_state):
    block = build_magic_context_block(magic_state=world_state, actor_id="sira_mendes")
    assert "hard_limits" in block
    # At least one hard limit ID appears
    limit_ids = [h.id for h in world_state.config.hard_limits]
    assert any(lid in block for lid in limit_ids)


def test_block_includes_actor_ledger(world_state):
    block = build_magic_context_block(magic_state=world_state, actor_id="sira_mendes")
    assert "sanity" in block
    assert "0.78" in block
    assert "notice" in block
    assert "0.22" in block


def test_block_includes_thresholds(world_state):
    block = build_magic_context_block(magic_state=world_state, actor_id="sira_mendes")
    # threshold_low for sanity = 0.40
    assert "0.40" in block or "0.4" in block


def test_block_includes_world_knowledge_with_subtag(world_state):
    block = build_magic_context_block(magic_state=world_state, actor_id="sira_mendes")
    assert "classified" in block
    assert "folkloric" in block


def test_block_instructs_narrator_to_emit_magic_working_field(world_state):
    block = build_magic_context_block(magic_state=world_state, actor_id="sira_mendes")
    assert "magic_working" in block


# ---------------------------------------------------------------------------
# Story 47-10 — Learned-magic block (AC7)
# ---------------------------------------------------------------------------
# When MagicState.prepared_spells[actor] is populated, the prompt context
# must render a learned-magic block listing known spells, prepared spells
# per level, and slots remaining. The narrator reads this and is bound by
# ADR-009 to not name an unprepared spell.


@pytest.fixture
def caster_state(world_config):
    """A MagicState with a Mage actor whose known/prepared/slot fields are
    populated as if seed_learned_v1_state ran + the player prepared two
    spells."""
    state = MagicState.from_config(world_config)
    state.add_character("rux")
    for sid in [
        "magic_missile", "sleep", "charm_person", "light", "read_magic",
        "detect_magic", "floating_disc", "hold_portal", "protection_from_evil",
        "read_languages", "shield", "ventriloquism",
    ]:
        state.learn_spell("rux", sid)
    state.prepare_spells("rux", {1: ["sleep", "magic_missile"]})
    return state


def test_block_includes_learned_magic_section_when_prepared(caster_state):
    """The learned-magic block (or whatever the renderer chooses) must
    appear when prepared_spells[actor] is non-empty."""
    block = build_magic_context_block(magic_state=caster_state, actor_id="rux")
    assert "learned-magic" in block or "prepared" in block.lower(), (
        f"Casters with prepared_spells must get a learned-magic context "
        f"block. Block contents:\n{block}"
    )


def test_block_lists_prepared_spells_by_id(caster_state):
    """The narrator must see the spell IDs to bind to — ADR-009 invariant
    (don't narrate unlisted actions)."""
    block = build_magic_context_block(magic_state=caster_state, actor_id="rux")
    assert "sleep" in block
    assert "magic_missile" in block


def test_block_separates_known_from_prepared(caster_state):
    """The block must distinguish 'known but not prepared' from 'prepared'
    so the narrator doesn't conflate them."""
    block = build_magic_context_block(magic_state=caster_state, actor_id="rux")
    # The prepared section should reference the prepared spells specifically.
    # An exact 'prepared' anchor is the simplest signal.
    assert "prepared" in block.lower(), (
        f"Block must label which spells are PREPARED vs KNOWN. Block:\n{block}"
    )


def test_block_renders_slot_count_for_prepared_level(caster_state):
    """The block must show slot info at L1 so the narrator knows when the
    Mage is out."""
    block = build_magic_context_block(magic_state=caster_state, actor_id="rux")
    block_lower = block.lower()
    assert "slot" in block_lower or "l1" in block_lower or "level 1" in block_lower, (
        f"learned-magic block must surface slot count / level info. Block:\n{block}"
    )
