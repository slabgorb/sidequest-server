"""Tests for the stable-section allowlist that drives system/user split."""

from __future__ import annotations

import pytest

from sidequest.agents.prompt_framework.bucket import (
    STABLE_SECTION_NAMES,
    SectionBucket,
    default_bucket_for_section,
)


def test_known_stable_sections_resolve_to_system():
    """Every name in the allowlist is bucketed as ``system``."""
    for name in STABLE_SECTION_NAMES:
        assert default_bucket_for_section(name) == SectionBucket.System, (
            f"{name!r} is in allowlist but did not resolve to System"
        )


def test_unknown_section_defaults_to_user():
    """A section name not in the allowlist defaults to ``user`` bucket.

    Safer default: dynamic content goes to user message. Stable scaffold
    requires explicit opt-in via the allowlist.
    """
    assert default_bucket_for_section("__never_registered_in_real_code") == SectionBucket.User


def test_allowlist_minimum_contents():
    """Pin the load-bearing stable-scaffold sections (spec §Composition).

    If a section moves out of system bucket, this test breaks loudly so
    the human reviewer sees the regression.
    """
    required = {
        "narrator_identity",
        "narrator_dialogue",
        "soul_principles",
        "output_format",
        "genre_identity",
        "genre_narrator_voice",
        "genre_npc_voice",
        "genre_world_state",
        "narrator_vocabulary",
        "genre_transition_hints",
    }
    missing = required - set(STABLE_SECTION_NAMES)
    assert not missing, f"Required stable sections missing from allowlist: {missing}"
