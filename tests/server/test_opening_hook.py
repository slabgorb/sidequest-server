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

from sidequest.game.persistence import GameMode
from sidequest.genre.loader import GenreLoader
from sidequest.genre.models.narrative import MpOpening, OpeningHook
from sidequest.genre.models.pack import GenrePack
from sidequest.server.dispatch.opening_hook import (
    _render_directive,
    _render_mp_directive,
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
    authored location. Without this, a Coyote-Star-style chargen close
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


# ---------------------------------------------------------------------------
# Multiplayer openings — see worlds/{slug}/mp_opening.yaml.
#
# MP precedence (playtest 2026-04-30 forensic on save
# `2026-04-30-coyote_star-mp/save.db`): when the session is in
# MULTIPLAYER mode and the world has authored ``mp_openings``, the MP
# tier wins over both world.openings and pack.openings. Solo and MP-
# without-mp_openings keep the legacy precedence chain. The pre-fix
# behavior dropped John alone into a `Firefight` confrontation while
# the other three Beatles ghosted — Agency violation across three of
# four players.
# ---------------------------------------------------------------------------


def _make_mp(
    id: str = "kestrel_galley",
    name: str = "Galley, Jump-Rest",
    establishing_narration: str = (
        "The Kestrel is mid-jump-rest. The galley is warm. "
        "The coffee is what passes for coffee."
    ),
    first_turn_invitation: str = "What does each of you do?",
    setting: dict | None = None,
    tone: dict | None = None,
    rig_voice_seeds: list[dict] | None = None,
    per_pc_beats: list[dict] | None = None,
    soft_hook: dict | None = None,
    party_framing: dict | None = None,
) -> MpOpening:
    return MpOpening(
        id=id,
        name=name,
        establishing_narration=establishing_narration,
        first_turn_invitation=first_turn_invitation,
        setting=setting or {"rig": "kestrel", "room": "galley"},
        tone=tone or {"register": "warm, lived-in, dry"},
        rig_voice_seeds=rig_voice_seeds or [],
        per_pc_beats=per_pc_beats or [],
        soft_hook=soft_hook or {},
        party_framing=party_framing or {},
    )


def test_mp_tier_preferred_when_mode_is_multiplayer(pack: GenrePack) -> None:
    """When the session is MP and the world has mp_openings, the MP tier
    wins over both world.openings and pack.openings.
    """
    world_slug = _first_world(pack)
    mp = _make_mp()
    world_hook = _make_hook(id="world-hook", archetype="world-arch")
    genre_hook = _make_hook(id="genre-hook", archetype="genre-arch")

    pack.worlds[world_slug].mp_openings = [mp]
    pack.worlds[world_slug].openings = [world_hook]
    pack.openings = [genre_hook]

    result = resolve_opening(
        pack,
        world_slug,
        "caverns_and_claudes",
        rng=random.Random(0),
        mode=GameMode.MULTIPLAYER,
    )
    assert result is not None
    seed, directive = result
    # Seed is the MP first_turn_invitation, not either OpeningHook seed.
    assert seed == "What does each of you do?"
    # Directive is rendered through the MP path (Mode: multiplayer marker).
    assert "Mode: multiplayer" in directive
    assert "world-arch" not in directive
    assert "genre-arch" not in directive


def test_solo_mode_ignores_mp_openings(pack: GenrePack) -> None:
    """Solo sessions never see MP openings, even when the world has them
    authored. Falls through to the legacy world-then-genre precedence.
    """
    world_slug = _first_world(pack)
    pack.worlds[world_slug].mp_openings = [_make_mp()]
    world_hook = _make_hook(id="world-hook", archetype="world-arch")
    pack.worlds[world_slug].openings = [world_hook]
    pack.openings = []

    result = resolve_opening(
        pack,
        world_slug,
        "caverns_and_claudes",
        rng=random.Random(0),
        mode=GameMode.SOLO,
    )
    assert result is not None
    seed, directive = result
    assert seed == world_hook.first_turn_seed
    assert "Mode: multiplayer" not in directive
    assert "world-arch" in directive


def test_mp_mode_falls_back_when_world_has_no_mp_openings(pack: GenrePack) -> None:
    """MP session against a world with no authored mp_openings falls
    through to the standard world-then-genre OpeningHook chain — solo
    and MP coexist when only solo content has been authored.
    """
    world_slug = _first_world(pack)
    pack.worlds[world_slug].mp_openings = []
    world_hook = _make_hook(id="world-hook", archetype="world-arch")
    pack.worlds[world_slug].openings = [world_hook]
    pack.openings = []

    result = resolve_opening(
        pack,
        world_slug,
        "caverns_and_claudes",
        rng=random.Random(0),
        mode=GameMode.MULTIPLAYER,
    )
    assert result is not None
    seed, directive = result
    assert seed == world_hook.first_turn_seed
    assert "Mode: multiplayer" not in directive


def test_mode_omitted_preserves_legacy_behavior(pack: GenrePack) -> None:
    """Calls without ``mode=`` (legacy non-slug connect path) keep the
    original world-then-genre precedence — never see mp_openings.
    """
    world_slug = _first_world(pack)
    pack.worlds[world_slug].mp_openings = [_make_mp()]
    world_hook = _make_hook(id="world-hook", archetype="world-arch")
    pack.worlds[world_slug].openings = [world_hook]
    pack.openings = []

    result = resolve_opening(
        pack, world_slug, "caverns_and_claudes", rng=random.Random(0)
    )
    assert result is not None
    seed, directive = result
    assert seed == world_hook.first_turn_seed
    assert "Mode: multiplayer" not in directive


def test_mp_directive_format_carries_authored_content() -> None:
    """The MP directive renderer must carry establishing narration,
    first turn invitation, avoid list, and party framing into the
    narrator's prompt — that's the contract that lets the directive
    replace solo-OpeningHook content for the chill MP opener.
    """
    mp = _make_mp(
        establishing_narration="The galley is warm. The coffee is what it is.",
        first_turn_invitation="What does each of you do?",
        tone={
            "register": "warm, lived-in, dry",
            "avoid_at_all_costs": ["any confrontation", "any dice roll"],
        },
        rig_voice_seeds=[
            {"context": "first PC enters", "line": "Mr. {first_name}. Coffee."},
        ],
        per_pc_beats=[
            {"applies_to": {"drive": "Saw Something Past the Gas Giant"},
             "beat": "Your nav log is still showing on the counter."},
        ],
        soft_hook={
            "timing": "if conversation lulls",
            "narration": "An inbound comm blinks once.",
        },
        party_framing={
            "already_a_crew": True,
            "bond_tier_default": "trusted",
            "shared_history_seeds": ["muscle memory from three jumps' worth of patch kits"],
        },
    )

    directive = _render_mp_directive(mp)

    # Bracketed identically to OpeningHook directives — content audits
    # and GM-panel regex both match on these markers.
    assert directive.startswith("=== OPENING SCENARIO ===")
    assert directive.endswith("=== END OPENING ===")
    assert "Mode: multiplayer" in directive

    # Establishing narration shows up under its own label, verbatim.
    assert "ESTABLISHING NARRATION" in directive
    assert "The galley is warm." in directive

    # First turn invitation lands under its label.
    assert "FIRST TURN INVITATION" in directive
    assert "What does each of you do?" in directive

    # Tone register and avoid list survive — narrator gets the guardrails.
    assert "Tone: warm, lived-in, dry" in directive
    assert "AVOID: any confrontation; any dice roll" in directive

    # Party framing — at minimum the "already a crew" marker reaches
    # the narrator so PCs aren't re-introduced to one another.
    assert "PARTY FRAMING" in directive
    assert "already a crew" in directive.lower()

    # Per-PC beat shows up keyed to its applies_to selector.
    assert "PER-PC BEATS" in directive
    assert "drive=Saw Something Past the Gas Giant" in directive
    assert "Your nav log is still showing on the counter." in directive

    # Soft hook clearly marked as conditional.
    assert "SOFT HOOK" in directive
    assert "if conversation lulls" in directive

    # Rig voice seeds show up so the narrator picks up the rig's register.
    assert "RIG VOICE SEEDS" in directive
    assert "Mr. {first_name}. Coffee." in directive


def test_mp_directive_minimal_when_only_required_fields_set() -> None:
    """An MpOpening with just id + establishing_narration still produces
    a coherent directive — optional sections are omitted, not rendered
    as empty stubs that confuse the narrator.
    """
    mp = MpOpening(
        id="bare",
        establishing_narration="The lights are on. Nobody's home yet.",
    )
    directive = _render_mp_directive(mp)
    assert directive.startswith("=== OPENING SCENARIO ===")
    assert "Mode: multiplayer" in directive
    assert "ESTABLISHING NARRATION" in directive
    # Optional sections are absent, not blank-stubbed.
    assert "PER-PC BEATS" not in directive
    assert "SOFT HOOK" not in directive
    assert "RIG VOICE SEEDS" not in directive
    assert "PARTY FRAMING" not in directive
    assert "AVOID:" not in directive
    assert "FIRST TURN INVITATION" not in directive


def test_mp_seed_uses_first_turn_invitation_when_present() -> None:
    """First turn invitation becomes the action string the narrator runs
    on turn 1 — same wiring contract as OpeningHook.first_turn_seed.
    Falls back to a generic establishing-scene instruction when an MP
    opening omits the invitation field.
    """
    from sidequest.server.dispatch.opening_hook import _mp_opening_seed

    mp = _make_mp(first_turn_invitation="What does each of you do?")
    assert _mp_opening_seed(mp) == "What does each of you do?"

    bare = MpOpening(id="bare", establishing_narration="…")
    fallback = _mp_opening_seed(bare)
    assert fallback  # never empty
    assert "Open the scene" in fallback


def test_loader_reads_real_coyote_star_mp_opening() -> None:
    """End-to-end check against the actual authored content. The
    loader picks up ``worlds/coyote_star/mp_opening.yaml`` and the
    resolver returns the Kestrel galley directive for an MP session.
    Guards against regressions where the world layer falls out of the
    loader's filename list.
    """
    space_opera_root = CONTENT_ROOT / "space_opera"
    if not space_opera_root.is_dir():
        pytest.skip("space_opera pack not present")
    space_pack = GenreLoader(search_paths=[CONTENT_ROOT]).load("space_opera")
    world = space_pack.worlds.get("coyote_star")
    if world is None:
        pytest.skip("coyote_star world not present")
    if not world.mp_openings:
        pytest.skip("coyote_star/mp_opening.yaml not authored")

    # The Kestrel galley opener is the first (and currently only) entry.
    assert world.mp_openings[0].id == "kestrel_galley_jumprest"

    result = resolve_opening(
        space_pack,
        "coyote_star",
        "space_opera",
        rng=random.Random(0),
        mode=GameMode.MULTIPLAYER,
    )
    assert result is not None
    seed, directive = result
    # The Kestrel directive carries the rig name and the chill-tone
    # guardrails — this is the load-bearing content per Keith's design
    # directive (no turn-1 confrontation, no dice).
    assert "kestrel" in directive.lower()
    assert "galley" in directive.lower()
    assert "AVOID:" in directive
    assert "any confrontation" in directive.lower()
    assert "any dice roll" in directive.lower()
    # Seed is the first-turn invitation — narrator opens with the
    # establishing scene and lands on this question.
    assert "what does each of you do" in seed.lower()
