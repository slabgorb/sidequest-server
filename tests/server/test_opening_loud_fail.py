"""Loud-fail watcher events for the opening-directive populator.

Bug: opening narration skips Kestrel beat (sq-playtest-pingpong.md, found
by Keith). Root cause traced by SM: ``_populate_opening_directive_on_
chargen_complete`` had four ``return  # defensive`` paths that ALL silently
bailed out — and one of them (``OpeningResolutionError`` via
min_players=2 unmet on first commit) was the active cause. Sebastien's GM
panel had no signal that the canned opening was even *attempted*; the
warning ``opening.skipped_reason=...`` never existed.

These tests pin the watcher emissions so the next regression surfaces
immediately. Per CLAUDE.md OTEL principle: every subsystem decision must
be observable from the GM panel.

Sibling fix: deferral gate (``_should_fire_opening_narration``) below.
The gate stops first committers in MP from getting improvised narration
when the canned MP opening can't resolve until the second PC commits.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import sidequest.server.session_handler  # noqa: F401 — ordering side-effect
from sidequest.server.websocket_session_handler import (
    _populate_opening_directive_on_chargen_complete,
    _should_fire_opening_narration,
)


@pytest.fixture
def captured_events(monkeypatch) -> list[tuple[str, dict, dict]]:
    """Capture every ``_watcher_publish`` call made during the test."""
    captured: list[tuple[str, dict, dict]] = []

    def fake_publish(
        event_type: str,
        fields: dict[str, Any],
        *,
        component: str = "",
        severity: str = "info",
    ) -> None:
        captured.append((event_type, fields, {"component": component, "severity": severity}))

    monkeypatch.setattr(
        "sidequest.server.websocket_session_handler._watcher_publish",
        fake_publish,
    )
    return captured


def _session_data(opening_directive: object | None = None) -> SimpleNamespace:
    """Minimal duck-typed _SessionData stand-in.

    The populator only reads ``opening_directive`` and writes
    ``opening_seed`` / ``opening_directive`` / ``_resolved_opening_id``.
    SimpleNamespace is sufficient — the real _SessionData has many more
    fields the populator never touches.
    """
    return SimpleNamespace(
        opening_directive=opening_directive,
        opening_seed=None,
        _resolved_opening_id=None,
        genre_slug="test_genre",
        world_slug="test_world",
        player_name="Tester",
    )


def _empty_snapshot() -> SimpleNamespace:
    return SimpleNamespace(characters=[])


def _snapshot_with_pc() -> SimpleNamespace:
    pc = SimpleNamespace(
        core=SimpleNamespace(name="Itchy"),
        background="Far Landing Raised Me",
        drive="vengeance",
        first_name="Itchy",
        last_name="Vasquez",
        nickname="",
    )
    return SimpleNamespace(characters=[pc])


def _pack_with_world(world: object | None) -> SimpleNamespace:
    """A pack whose .worlds.get(world_slug) returns ``world``."""
    return SimpleNamespace(worlds={"test_world": world} if world is not None else {})


def _world_with_openings(openings: list) -> SimpleNamespace:
    return SimpleNamespace(
        openings=openings,
        chassis_instances=[],
        authored_npcs=[],
        magic_register="",
    )


# ---- loud-fail watcher events ----------------------------------------


def test_populate_emits_skip_event_on_empty_snapshot(captured_events) -> None:
    """Empty snapshot at populator entry must emit
    ``opening.skipped_reason=empty_snapshot`` (was a silent ``return
    # defensive``).
    """
    sd = _session_data()
    _populate_opening_directive_on_chargen_complete(
        session_data=sd,
        snapshot=_empty_snapshot(),
        pack=_pack_with_world(_world_with_openings([])),
        world_slug="test_world",
        mode="multiplayer",
    )

    skip_events = [
        (fields, meta) for et, fields, meta in captured_events if et == "opening.skipped"
    ]
    assert skip_events, (
        "expected opening.skipped watcher event on empty-snapshot bail; "
        f"captured: {captured_events}"
    )
    fields, meta = skip_events[0]
    assert fields["reason"] == "empty_snapshot"
    assert meta["component"] == "opening_hook"
    assert meta["severity"] == "warning"
    # Populator did NOT populate (preserves prior return-without-side-effect).
    assert sd.opening_directive is None
    assert sd.opening_seed is None


def test_populate_emits_skip_event_when_world_missing(captured_events) -> None:
    """Pack with no matching world → ``reason=world_or_openings_missing``.

    Validator-7 should make this unreachable at load time, but the
    populator still has the defensive branch — it should now be loud.
    """
    sd = _session_data()
    _populate_opening_directive_on_chargen_complete(
        session_data=sd,
        snapshot=_snapshot_with_pc(),
        pack=_pack_with_world(None),
        world_slug="test_world",
        mode="multiplayer",
    )
    skip_events = [
        (fields, meta) for et, fields, meta in captured_events if et == "opening.skipped"
    ]
    assert skip_events
    fields, meta = skip_events[0]
    assert fields["reason"] == "world_or_openings_missing"
    assert meta["severity"] == "warning"


def test_populate_emits_skip_event_when_world_has_no_openings(captured_events) -> None:
    """World present but ``openings=[]`` → ``reason=world_or_openings_missing``."""
    sd = _session_data()
    _populate_opening_directive_on_chargen_complete(
        session_data=sd,
        snapshot=_snapshot_with_pc(),
        pack=_pack_with_world(_world_with_openings([])),
        world_slug="test_world",
        mode="multiplayer",
    )
    skip_events = [
        (fields, meta) for et, fields, meta in captured_events if et == "opening.skipped"
    ]
    assert skip_events
    fields, meta = skip_events[0]
    assert fields["reason"] == "world_or_openings_missing"


def test_populate_emits_skip_event_on_resolution_failed(captured_events, monkeypatch) -> None:
    """The Kestrel-skip bug's *actual* trigger: opening bank exists but
    ``_resolve_opening_post_chargen`` raises ``OpeningResolutionError``
    because the only matching opening has ``min_players=2`` and only
    one PC has committed yet. Must emit
    ``opening.skipped_reason=resolution_failed`` so Sebastien sees
    "the resolver tried, no opening matched, here's why" instead of
    silence + improvised customs prose.
    """
    from sidequest.server.dispatch.opening import OpeningResolutionError

    fake_opening = SimpleNamespace(id="mp_galley_jumprest")  # not actually returned

    def boom(*args, **kwargs):
        raise OpeningResolutionError("no opening matches mode=multiplayer player_count=1")

    monkeypatch.setattr(
        "sidequest.server.websocket_session_handler._resolve_opening_post_chargen",
        boom,
    )

    sd = _session_data()
    _populate_opening_directive_on_chargen_complete(
        session_data=sd,
        snapshot=_snapshot_with_pc(),
        pack=_pack_with_world(_world_with_openings([fake_opening])),
        world_slug="test_world",
        mode="multiplayer",
    )

    skip_events = [
        (fields, meta) for et, fields, meta in captured_events if et == "opening.skipped"
    ]
    assert skip_events
    fields, meta = skip_events[0]
    assert fields["reason"] == "resolution_failed"
    # Error detail surfaces so Sebastien can read why the resolver gave up.
    assert "no opening matches" in fields.get("error", "")
    assert meta["severity"] == "warning"
    # Populator did NOT populate.
    assert sd.opening_directive is None


def test_populate_already_populated_is_silent(captured_events) -> None:
    """Idempotency: when ``opening_directive`` is already set (double
    confirmation, replay), the populator returns silently — no skip
    event, no resolved event. Prevents log spam on repeated commits.
    """
    sd = _session_data(opening_directive=object())
    _populate_opening_directive_on_chargen_complete(
        session_data=sd,
        snapshot=_snapshot_with_pc(),
        pack=_pack_with_world(_world_with_openings([])),
        world_slug="test_world",
        mode="multiplayer",
    )
    assert captured_events == []


# ---- deferral gate ---------------------------------------------------


def test_should_fire_opening_solo_no_room() -> None:
    """Solo path with no MP room — opening always fires on first commit."""
    sd = SimpleNamespace(
        opening_directive=None,
        snapshot=SimpleNamespace(characters=[object()]),
    )
    assert _should_fire_opening_narration(sd, room=None) is True


def test_should_fire_opening_when_directive_already_resolved() -> None:
    """If the populator successfully built a directive (party complete
    OR solo opening matched), opening narration should fire — even if
    the room bookkeeping somehow says otherwise. Directive-presence is
    the strong signal.
    """
    room = SimpleNamespace(non_abandoned_player_count=lambda: 2)
    sd = SimpleNamespace(
        opening_directive=object(),
        snapshot=SimpleNamespace(characters=[object()]),
    )
    assert _should_fire_opening_narration(sd, room=room) is True


def test_should_defer_opening_mp_first_committer_no_directive() -> None:
    """The bug shape: MP, 2 seats expected, only 1 PC committed,
    populator failed (no directive) — defer.
    """
    room = SimpleNamespace(non_abandoned_player_count=lambda: 2)
    sd = SimpleNamespace(
        opening_directive=None,
        snapshot=SimpleNamespace(characters=[object()]),  # only 1 PC committed
    )
    assert _should_fire_opening_narration(sd, room=room) is False


def test_should_fire_opening_mp_last_committer() -> None:
    """The reverse: MP 2 seats, 2 PCs committed (last committer's call) —
    fire even when this particular sd has no directive yet (the caller
    populates immediately before the gate check, so the directive
    should be set; this test pins the count-matches branch as a
    belt-and-suspenders fallback).
    """
    room = SimpleNamespace(non_abandoned_player_count=lambda: 2)
    sd = SimpleNamespace(
        opening_directive=None,
        snapshot=SimpleNamespace(characters=[object(), object()]),
    )
    assert _should_fire_opening_narration(sd, room=room) is True


def test_should_fire_opening_room_reports_one_player() -> None:
    """Edge case: room reports ``non_abandoned_player_count=1`` (solo via
    MP-room-of-one — pre-MP saves loaded into a fresh room). Treat as
    solo: fire immediately.
    """
    room = SimpleNamespace(non_abandoned_player_count=lambda: 1)
    sd = SimpleNamespace(
        opening_directive=None,
        snapshot=SimpleNamespace(characters=[object()]),
    )
    assert _should_fire_opening_narration(sd, room=room) is True
