"""Pingpong 2026-05-07 wiring tests: ``magic.init`` MUST fire for every
MP committer, not just the host.

Background — three-player C&C playtest of 47-2 surfaced two failures:

  1. Carl (host, Cleric) got ``magic.init`` (with the genre's actual
     plugin list).
  2. Donut (joiner, Fighter) got no ``magic.init`` — possibly correct
     for Fighter on an item-only world, but the absence is silent.
  3. Katia (joiner, Mage) got no ``magic.init`` — UNEXPECTED. The MP
     second-commit code path appended Katia to ``snapshot.characters``
     but never called ``init_magic_state_for_session``, so her actor
     row was missing from ``snapshot.magic_state.ledger`` and any
     narrator-emitted working against her would raise ``unknown
     actor; call add_character first`` (the exact 2026-04-30 shape the
     idempotence fix was supposed to close — at the time only ONE
     call site existed).

This test file pins the wiring at the source-grep level (so a future
refactor cannot silently un-thread the hook from the second-commit
branch) AND at the behavioral level (a snapshot that already has a
magic_state from the first committer must, after the second-commit
helper code path, contain the joiner in the ledger).
"""

from __future__ import annotations

from pathlib import Path

from sidequest.game.session import GameSnapshot
from sidequest.server.magic_init import init_magic_state_for_session

CONTENT_ROOT = Path(__file__).resolve().parents[2].parent / "sidequest-content" / "genre_packs"


def test_websocket_session_handler_calls_magic_init_in_mp_second_commit() -> None:
    """Wire-first source grep: the MP second-commit branch (the ``else``
    arm of ``is_first_commit``) must call ``init_magic_state_for_session``
    so late joiners are registered in the magic ledger and the
    ``magic.init`` OTEL span fires for them.

    Pre-fix the second-commit branch only appended the PC to
    ``sd.snapshot.characters`` and emitted a ``mp_world_reused`` span;
    no magic-state touch.
    """
    from sidequest.server import websocket_session_handler

    with open(websocket_session_handler.__file__) as fh:
        source = fh.read()

    # The first-commit branch already calls init_magic_state_for_session;
    # the second-commit branch must too. Count function-CALL occurrences
    # (the trailing ``(``) — pre-fix exactly one call site existed
    # (line ~1407 in the first-commit branch); post-fix there must be
    # at least two (one per chargen-commit branch).
    call_count = source.count("init_magic_state_for_session(")
    assert call_count >= 2, (
        f"Expected at least 2 call sites for init_magic_state_for_session "
        f"(first-commit branch + MP second-commit branch), found "
        f"{call_count}. The MP second-commit branch is unwired — "
        f"late joiners will be missing from snapshot.magic_state.ledger."
    )


def test_websocket_session_handler_passes_character_class_in_both_chargen_commits() -> None:
    """Every chargen-commit ``init_magic_state_for_session`` call must pass
    ``character_class=`` so class-keyed magic bars (``starts_at_chargen``
    keyed by class — caverns_sunden 2026-05-07) initialise correctly.

    Pre-fix the MP second-commit branch passed only ``character_id``;
    on a class-keyed world (caverns_sunden Mage/Cleric spell slots) this
    raised ``ValueError: bar 'spell_slots' has class-keyed
    starts_at_chargen but no character_class was supplied for character
    'Tester'`` from inside ``MagicState.add_character``. The host branch
    was correct; the joiner branch was the regression seam.

    Counts the literal ``character_class=`` substring against the call
    count — every call must include the kwarg. There is also a
    helper-level call (``_load_class_def``) that legitimately uses
    ``character_class`` as a positional/keyword arg name; it lives
    outside ``init_magic_state_for_session(`` invocations so the
    counting strategy below (slice each call's argument block) is robust
    to that.
    """
    from sidequest.server import websocket_session_handler

    with open(websocket_session_handler.__file__) as fh:
        source = fh.read()

    # Find every ``init_magic_state_for_session(`` call and slice out its
    # argument block (up to the matching close-paren). Then assert the
    # block contains ``character_class=``.
    needle = "init_magic_state_for_session("
    cursor = 0
    call_blocks: list[str] = []
    while True:
        start = source.find(needle, cursor)
        if start < 0:
            break
        # Walk forward to the matching close-paren (depth-tracked).
        i = start + len(needle)
        depth = 1
        while i < len(source) and depth > 0:
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
            i += 1
        call_blocks.append(source[start:i])
        cursor = i

    assert len(call_blocks) >= 2, (
        f"Expected at least 2 call blocks; found {len(call_blocks)}. "
        f"See the prior test for the wire requirement."
    )

    for block in call_blocks:
        assert "character_class=" in block, (
            f"init_magic_state_for_session call block missing "
            f"`character_class=` kwarg:\n{block}\n"
            f"Class-keyed magic bars (caverns_sunden) raise ValueError "
            f"inside add_character when character_class is omitted."
        )


def test_init_magic_state_registers_mp_joiner_after_host_commit() -> None:
    """End-to-end behavioral guard: after the host's chargen commit
    populates ``snapshot.magic_state``, a joiner's chargen commit must
    add the joiner to the SAME ledger via the same helper.

    Uses Coyote Star (space_opera) because it ships canonical
    per-character bars (sanity/notice/vitality on innate_v1) — exactly
    the surface the 47-2 playtest expectation was checking. C&C is
    item-only by genre design and would have ``bars=0`` even with the
    helper called, which doesn't exercise the per-character ledger
    path.
    """
    pack_dir = CONTENT_ROOT / "space_opera"
    world_slug = "coyote_star"
    assert (pack_dir / "magic.yaml").is_file()
    assert (pack_dir / "worlds" / world_slug / "magic.yaml").is_file()

    snap = GameSnapshot(genre_slug="space_opera", world_slug=world_slug)

    # Host commits first.
    init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=pack_dir,
        world_slug=world_slug,
        character_id="HostPC",
    )
    assert snap.magic_state is not None
    host_keys = [k for k in snap.magic_state.ledger if k.startswith("character|HostPC|")]
    assert len(host_keys) > 0, "host PC must have per-character bars after commit"

    # Joiner commits second — same canonical snapshot.
    init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=pack_dir,
        world_slug=world_slug,
        character_id="JoinerPC",
    )
    joiner_keys = [k for k in snap.magic_state.ledger if k.startswith("character|JoinerPC|")]
    assert len(joiner_keys) > 0, (
        "Joiner PC missing from magic_state.ledger after second-commit "
        "init. Pre-fix this seam was unwired in "
        "websocket_session_handler.py and joiners would hit "
        "'unknown actor; call add_character first' on the first "
        "narrator working that referenced them."
    )

    # Host bars must NOT have been wiped (idempotence on snapshot.magic_state).
    host_keys_after = [k for k in snap.magic_state.ledger if k.startswith("character|HostPC|")]
    assert len(host_keys_after) == len(host_keys), (
        "Joiner commit must REUSE the host's magic_state, not replace it. "
        "Replacement would wipe the host's per-character bars."
    )


def test_magic_init_emits_otel_watcher_event(monkeypatch) -> None:
    """OTEL Observability Principle (CLAUDE.md): every backend subsystem
    fix MUST add watcher events so the GM panel can verify the fix is
    engaged. ``magic.init`` was logger-only pre-fix; this test pins the
    new watcher span emission so a future refactor cannot silently
    drop it.

    Pattern matches tests/magic/test_magic_span.py fixture: monkeypatch
    the module-local ``_watcher_publish`` symbol to capture instead of
    binding an async loop + fake subscriber.
    """
    from sidequest.server import magic_init as magic_init_mod

    captured: list[dict] = []

    def _capture(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append(
            {
                "event_type": event_type,
                "fields": fields,
                "component": component,
                "severity": severity,
            }
        )

    monkeypatch.setattr(magic_init_mod, "_watcher_publish", _capture)

    pack_dir = CONTENT_ROOT / "space_opera"
    world_slug = "coyote_star"
    snap = GameSnapshot(genre_slug="space_opera", world_slug=world_slug)

    init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=pack_dir,
        world_slug=world_slug,
        character_id="OtelPC",
    )

    init_events = [e for e in captured if e["event_type"] == "magic.init"]
    assert init_events, (
        f"magic.init watcher event was not emitted. Without this span "
        f"the GM panel cannot verify subsystem engagement, which is "
        f"the lie-detector failure CLAUDE.md OTEL Observability "
        f"Principle was designed to catch. Got events: "
        f"{[e['event_type'] for e in captured]}"
    )
    fields = init_events[0]["fields"]
    assert fields.get("actor") == "OtelPC"
    assert fields.get("world_slug") == world_slug
    assert "plugins" in fields
    assert "bars" in fields
    assert init_events[0]["component"] == "magic"


def test_magic_init_skipped_emits_otel_watcher_event(monkeypatch, tmp_path: Path) -> None:
    """When magic init is skipped (no magic.yaml for this world), the
    GM panel needs a ``magic.init_skipped`` event with a reason —
    otherwise "subsystem invisible" reads as "subsystem broken". This
    closes the silent-fallback gap CLAUDE.md forbids.
    """
    from sidequest.server import magic_init as magic_init_mod

    captured: list[dict] = []

    def _capture(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append({"event_type": event_type, "fields": fields})

    monkeypatch.setattr(magic_init_mod, "_watcher_publish", _capture)

    fake_pack = tmp_path / "fake_pack"
    (fake_pack / "worlds" / "fake_world").mkdir(parents=True)
    snap = GameSnapshot(genre_slug="fake", world_slug="fake_world")

    ok = init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=fake_pack,
        world_slug="fake_world",
        character_id="anyone",
    )
    assert ok is False

    skip_events = [e for e in captured if e["event_type"] == "magic.init_skipped"]
    assert skip_events, (
        f"magic.init_skipped watcher event was not emitted on the "
        f"no-magic-yaml path. Silent skip + silent absence of an event "
        f"is exactly the silent-fallback shape CLAUDE.md forbids. "
        f"Got events: {[e['event_type'] for e in captured]}"
    )
    assert skip_events[0]["fields"].get("reason") == "no_magic_yaml"
