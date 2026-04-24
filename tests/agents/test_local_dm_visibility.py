"""Tests for apply_visibility_baseline — Group G Task 4.

The decomposer calls this per-dispatch after parse to fill in VisibilityTag
defaults from the session's VisibilityBaseline.
"""
from __future__ import annotations

from sidequest.agents.local_dm import apply_visibility_baseline
from sidequest.genre.models.visibility import VisibilityBaseline


BASELINE_SECRET = VisibilityBaseline.model_validate_yaml("""
tone: secret_heavy
default_visibility:
  stealth_roll_check: actor_only
  npc_agency: actor_only
  lore_reveal: actor_only
all_scope: protagonists
""")


def test_stealth_roll_defaults_to_actor_only():
    dispatch = {
        "subsystem": "stealth_roll_check",
        "params": {"actor": "player:Alice"},
        "idempotency_key": "k1",
        "visibility": {"visible_to": "all", "perception_fidelity": {},
                       "secrets_for": [], "redact_from_narrator_canonical": False},
    }
    applied = apply_visibility_baseline(
        dispatch, baseline=BASELINE_SECRET, actor_player_id="player:Alice",
    )
    assert applied["visibility"]["visible_to"] == ["player:Alice"]


def test_dispatch_with_explicit_tag_is_untouched():
    dispatch = {
        "subsystem": "stealth_roll_check",
        "params": {"actor": "player:Alice"},
        "idempotency_key": "k1",
        "visibility": {"visible_to": ["player:Alice", "player:Bob"],
                       "perception_fidelity": {}, "secrets_for": [],
                       "redact_from_narrator_canonical": False},
        "_visibility_explicit": True,
    }
    applied = apply_visibility_baseline(
        dispatch, baseline=BASELINE_SECRET, actor_player_id="player:Alice",
    )
    assert applied["visibility"]["visible_to"] == ["player:Alice", "player:Bob"]


def test_unknown_subsystem_leaves_all():
    dispatch = {
        "subsystem": "confrontation_init",   # not in our test baseline
        "params": {"actor": "player:Alice"},
        "idempotency_key": "k1",
        "visibility": {"visible_to": "all", "perception_fidelity": {},
                       "secrets_for": [], "redact_from_narrator_canonical": False},
    }
    applied = apply_visibility_baseline(
        dispatch, baseline=BASELINE_SECRET, actor_player_id="player:Alice",
    )
    assert applied["visibility"]["visible_to"] == "all"
