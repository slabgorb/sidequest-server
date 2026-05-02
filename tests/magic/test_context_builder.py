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
