"""Tests for ``sidequest.server.dispatch.opening_hook.resolve_opening``.

Covers:
- World-tier openings preferred over genre-tier when both present.
- Genre-tier fallback when world has no openings.
- ``None`` return when neither tier has openings.
- Directive format matches Rust parity (header/archetype/situation/tone/
  avoid/footer).
- AVOID line omitted when ``hook.avoid`` is empty.
- RNG is used for selection (seeded RNG produces deterministic output).

Uses a real loaded caverns_and_claudes genre pack and patches the
``openings`` lists rather than hand-scaffolding a minimal pack (too
many required fields across the model tree — fragile).
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from sidequest.genre.loader import GenreLoader
from sidequest.genre.models.narrative import OpeningHook
from sidequest.genre.models.pack import GenrePack
from sidequest.server.dispatch.opening_hook import (
    _render_directive,
    resolve_opening,
)

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


def _make_hook(
    id: str = "hook-a",
    archetype: str = "wary-traveler",
    situation: str = "The road forks under a blood moon.",
    tone: str = "ominous",
    avoid: list[str] | None = None,
    first_turn_seed: str = "You stand at the fork, breath fogging.",
) -> OpeningHook:
    return OpeningHook(
        id=id,
        archetype=archetype,
        situation=situation,
        tone=tone,
        avoid=avoid if avoid is not None else [],
        first_turn_seed=first_turn_seed,
    )


@pytest.fixture
def pack() -> GenrePack:
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("content pack not found")
    return GenreLoader(search_paths=[CONTENT_ROOT]).load("caverns_and_claudes")


def _first_world(pack: GenrePack) -> str:
    return next(iter(pack.worlds.keys()))


def test_world_tier_preferred_over_genre_tier(pack: GenrePack) -> None:
    world_slug = _first_world(pack)
    world_hook = _make_hook(id="world-hook", archetype="world-arch")
    genre_hook = _make_hook(id="genre-hook", archetype="genre-arch")

    pack.worlds[world_slug].openings = [world_hook]
    pack.openings = [genre_hook]

    result = resolve_opening(pack, world_slug, "caverns_and_claudes", rng=random.Random(0))
    assert result is not None
    seed, directive = result
    assert seed == world_hook.first_turn_seed
    assert "world-arch" in directive
    assert "genre-arch" not in directive


def test_falls_back_to_genre_tier_when_world_has_none(pack: GenrePack) -> None:
    world_slug = _first_world(pack)
    genre_hook = _make_hook(id="genre-hook", archetype="genre-arch")

    pack.worlds[world_slug].openings = []
    pack.openings = [genre_hook]

    result = resolve_opening(pack, world_slug, "caverns_and_claudes", rng=random.Random(0))
    assert result is not None
    seed, directive = result
    assert seed == genre_hook.first_turn_seed
    assert "genre-arch" in directive


def test_returns_none_when_no_openings_anywhere(pack: GenrePack) -> None:
    world_slug = _first_world(pack)
    pack.worlds[world_slug].openings = []
    pack.openings = []

    result = resolve_opening(pack, world_slug, "caverns_and_claudes", rng=random.Random(0))
    assert result is None


def test_returns_none_when_world_slug_missing_and_no_genre(pack: GenrePack) -> None:
    # World slug that isn't in pack.worlds falls through to genre tier;
    # when genre tier is also empty, nothing to return.
    pack.openings = []
    result = resolve_opening(
        pack, "nonexistent_world", "caverns_and_claudes", rng=random.Random(0)
    )
    assert result is None


def test_directive_format_matches_rust_parity() -> None:
    hook = _make_hook(
        archetype="lost-sage",
        situation="A ritual bell tolls at midnight.",
        tone="uncanny",
        avoid=["clockwork villains", "plain combat"],
    )
    directive = _render_directive(hook)
    expected = (
        "=== OPENING SCENARIO ===\n"
        "Archetype: lost-sage\n"
        "Situation: A ritual bell tolls at midnight.\n"
        "Tone: uncanny\n"
        "AVOID: clockwork villains; plain combat\n"
        "=== END OPENING ==="
    )
    assert directive == expected


def test_directive_omits_avoid_when_empty() -> None:
    hook = _make_hook(avoid=[])
    directive = _render_directive(hook)
    assert "AVOID:" not in directive
    assert directive.endswith("=== END OPENING ===")


def test_directive_with_setting_injects_world_starting_location() -> None:
    """Playtest 2026-04-30 "Setting drift" regression.

    When the world declares ``starting_location``, the directive must
    carry a ``Setting:`` line so the narrator's first turn opens at the
    authored location. Without this, a Coyote-Reach-style chargen close
    that promises "Far Landing is just waking up around you" gets
    overridden by a genre-tier opening that lands the player on an
    unrelated orbital station — a Diamonds-and-Coal violation.
    """
    hook = _make_hook(
        archetype="frontier-hook",
        situation="A miner walks into the post with a problem.",
        tone="weary",
    )
    directive = _render_directive(
        hook,
        setting_label="Far Landing",
        starting_time="morning",
    )
    assert "Setting: Far Landing, morning (open the scene here)" in directive, (
        f"directive missing Setting line: {directive!r}"
    )
    # Existing structure preserved — Setting line slots between Tone and AVOID/footer.
    assert directive.startswith("=== OPENING SCENARIO ===")
    assert directive.endswith("=== END OPENING ===")


def test_directive_setting_omits_time_when_unknown() -> None:
    """Worlds without an authored ``starting_time`` still get a clean
    Setting line — no dangling comma, no empty parens.
    """
    hook = _make_hook()
    directive = _render_directive(hook, setting_label="Far Landing", starting_time=None)
    assert "Setting: Far Landing (open the scene here)" in directive
    assert "Setting: Far Landing,," not in directive  # no double comma


def test_directive_omits_setting_when_label_blank() -> None:
    """Empty / None label → no Setting line; older worlds keep the
    Rust-parity directive shape exactly.
    """
    hook = _make_hook()
    directive_none = _render_directive(hook, setting_label=None)
    directive_empty = _render_directive(hook, setting_label="")
    for d in (directive_none, directive_empty):
        assert "Setting:" not in d, f"unexpected Setting line in {d!r}"


def test_resolve_opening_pulls_setting_from_world_config(pack: GenrePack) -> None:
    """End-to-end: when ``world.config.starting_location`` is set and
    cartography has the room, ``resolve_opening`` produces a directive
    carrying the resolved display name.
    """
    world_slug = _first_world(pack)
    pack.worlds[world_slug].openings = [
        _make_hook(id="setting-test", archetype="setting-arch")
    ]
    # Force a known starting_location and confirm cartography resolution.
    cart = pack.worlds[world_slug].cartography
    rooms = getattr(cart, "rooms", None) or []
    if not rooms:
        pytest.skip("test pack world has no cartography rooms to resolve against")
    target_room = rooms[0]
    pack.worlds[world_slug].config.starting_location = target_room.id

    result = resolve_opening(
        pack, world_slug, "caverns_and_claudes", rng=random.Random(0)
    )
    assert result is not None
    _, directive = result
    assert f"Setting: {target_room.name}" in directive, (
        f"resolve_opening did not surface starting_location into the directive: "
        f"{directive!r}"
    )


def test_resolve_opening_omits_setting_when_world_has_no_starting_location(
    pack: GenrePack,
) -> None:
    """Worlds that don't declare ``starting_location`` keep producing
    the older directive shape — the Setting line is opt-in via content.
    """
    world_slug = _first_world(pack)
    pack.worlds[world_slug].openings = [
        _make_hook(id="no-setting", archetype="no-setting-arch")
    ]
    pack.worlds[world_slug].config.starting_location = ""

    result = resolve_opening(
        pack, world_slug, "caverns_and_claudes", rng=random.Random(0)
    )
    assert result is not None
    _, directive = result
    assert "Setting:" not in directive, (
        f"directive grew an unexpected Setting line: {directive!r}"
    )


def test_seeded_rng_is_deterministic(pack: GenrePack) -> None:
    world_slug = _first_world(pack)
    hooks = [
        _make_hook(id=f"hook-{i}", archetype=f"arch-{i}", first_turn_seed=f"seed-{i}")
        for i in range(5)
    ]
    pack.worlds[world_slug].openings = []
    pack.openings = hooks

    r1 = resolve_opening(pack, world_slug, "caverns_and_claudes", rng=random.Random(42))
    r2 = resolve_opening(pack, world_slug, "caverns_and_claudes", rng=random.Random(42))
    assert r1 == r2

    # Different seed → different pick. Sweep a few to be robust against
    # rare collisions; at least one must differ from seed=42's pick.
    different_seeds = [r1 != resolve_opening(
        pack, world_slug, "caverns_and_claudes", rng=random.Random(s)
    ) for s in [1, 3, 7, 11, 13]]
    assert any(different_seeds), (
        "selection appears insensitive to the RNG seed"
    )
