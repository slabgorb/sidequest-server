"""Tests for _resolve_opening_post_chargen — picks an Opening from the
world bank by mode, player_count, and PC background."""

from __future__ import annotations

import random

import pytest

from sidequest.genre.models.narrative import (
    Opening,
    OpeningSetting,
    OpeningTrigger,
)
from sidequest.server.dispatch.opening import (
    OpeningResolutionError,
    _resolve_opening_post_chargen,
)


def _opening(
    id: str,
    *,
    mode: str = "solo",
    backgrounds: list[str] | None = None,
    min_p: int = 1,
    max_p: int = 6,
) -> Opening:
    return Opening(
        id=id,
        triggers=OpeningTrigger(
            mode=mode,
            backgrounds=backgrounds or [],
            min_players=min_p,
            max_players=max_p,
        ),
        setting=OpeningSetting(chassis_instance="kestrel", interior_room="galley"),
        establishing_narration="The galley is warm.",
        first_turn_invitation="Outside the porthole, void.",
    )


def test_solo_mode_filter() -> None:
    bank = [
        _opening("a", mode="solo"),
        _opening("b", mode="multiplayer"),
        _opening("c", mode="either"),
    ]
    chosen = _resolve_opening_post_chargen(
        bank, mode="solo", player_count=1, pc_background="X",
        rng=random.Random(0),
    )
    assert chosen.id in {"a", "c"}


def test_mp_mode_filter() -> None:
    bank = [
        _opening("a", mode="solo"),
        _opening("b", mode="multiplayer"),
        _opening("c", mode="either"),
    ]
    chosen = _resolve_opening_post_chargen(
        bank, mode="multiplayer", player_count=3, pc_background="X",
        rng=random.Random(0),
    )
    assert chosen.id in {"b", "c"}


def test_background_keyed_selection() -> None:
    bank = [
        _opening("far_landing", mode="solo", backgrounds=["Far Landing Raised Me"]),
        _opening("hub", mode="solo", backgrounds=["Turning Hub Was the Whole World"]),
    ]
    chosen = _resolve_opening_post_chargen(
        bank, mode="solo", player_count=1,
        pc_background="Far Landing Raised Me",
        rng=random.Random(0),
    )
    assert chosen.id == "far_landing"


def test_background_fallback_when_no_keyed_match() -> None:
    bank = [
        _opening("far_landing", mode="solo", backgrounds=["Far Landing Raised Me"]),
        _opening("fallback", mode="solo", backgrounds=[]),
    ]
    chosen = _resolve_opening_post_chargen(
        bank, mode="solo", player_count=1,
        pc_background="Unknown Background",
        rng=random.Random(0),
    )
    assert chosen.id == "fallback"


def test_keyed_preferred_over_fallback() -> None:
    """When both a keyed entry and a fallback match, keyed wins."""
    bank = [
        _opening("far_landing", mode="solo", backgrounds=["Far Landing Raised Me"]),
        _opening("fallback", mode="solo", backgrounds=[]),
    ]
    chosen = _resolve_opening_post_chargen(
        bank, mode="solo", player_count=1,
        pc_background="Far Landing Raised Me",
        rng=random.Random(0),
    )
    assert chosen.id == "far_landing"


def test_player_count_filter() -> None:
    bank = [
        _opening("two_player", mode="multiplayer", min_p=2, max_p=2),
        _opening("any_size", mode="multiplayer", min_p=1, max_p=6),
    ]
    chosen = _resolve_opening_post_chargen(
        bank, mode="multiplayer", player_count=4, pc_background="X",
        rng=random.Random(0),
    )
    assert chosen.id == "any_size"


def test_no_match_raises() -> None:
    bank = [_opening("solo_only", mode="solo")]
    with pytest.raises(OpeningResolutionError):
        _resolve_opening_post_chargen(
            bank, mode="multiplayer", player_count=2, pc_background="X",
            rng=random.Random(0),
        )


def test_deterministic_with_seeded_rng() -> None:
    bank = [
        _opening("a", mode="solo"),
        _opening("b", mode="solo"),
        _opening("c", mode="solo"),
    ]
    chosen1 = _resolve_opening_post_chargen(
        bank, mode="solo", player_count=1, pc_background="X",
        rng=random.Random(42),
    )
    chosen2 = _resolve_opening_post_chargen(
        bank, mode="solo", player_count=1, pc_background="X",
        rng=random.Random(42),
    )
    assert chosen1.id == chosen2.id
