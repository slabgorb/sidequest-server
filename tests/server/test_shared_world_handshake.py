"""Wire-first boundary tests for Story 45-1 — Sealed-letter world-state
handshake (shared-world delta between turns).

Re-scope of 37-37 onto the live Python tree (ADR-085). After each player
turn resolves, the server emits a minimal shared-world delta (current
location, active encounter id, party formation/adjacency) which seeds
the next player's turn so the narrator stops fabricating physical
separations to explain party-mate absence.

Playtest 3 evidence (2026-04-19): Orin's narrator invented a "collapsed
corridor" separating Orin from Blutka because Orin's ``state_summary``
JSON had no ground-truth that Blutka was in the same room.

Boundary surfaces exercised here:
- ``_build_turn_context`` — the outermost reachable apply seam for the
  narrator. Its ``state_summary`` JSON is the ground truth the LLM sees.
  This is the wire-first boundary test for AC2.
- ``build_shared_world_delta`` / ``merge_shared_delta_into_snapshot`` —
  the producer/consumer pair AC1 and AC4 contract against. Helper-level
  unit tests are SUPPORT for the boundary test, not a substitute.
- ``sidequest.telemetry.spans.SPAN_GAME_HANDSHAKE_DELTA_APPLIED`` — the
  GM-panel lie detector (CLAUDE.md observability principle). Constant +
  SPAN_ROUTES entry must exist or the OTEL dashboard renders nothing.
- Source-level wiring assertions on ``session_handler.py`` and
  ``session_helpers.py`` — wire-first project rule: every new ``pub``
  export has at least one non-test consumer in the same PR diff. These
  source-grep tests are the cheap gate that catches dead code.

These tests are RED until story 45-1 lands.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from sidequest.game.persistence import GameMode


# ---------------------------------------------------------------------------
# AC1 + AC4 — Producer-side: build_shared_world_delta yields canonical-only
# fields. This is the contract the broadcast carries.
# ---------------------------------------------------------------------------


def test_build_shared_world_delta_carries_canonical_fields(
    session_handler_factory,
) -> None:
    """``build_shared_world_delta(snapshot, room=...)`` returns a payload
    with ``location``, ``encounter_id``, and ``party_formation``. Every
    seated player_id appears in party_formation with a location.

    Until story 45-1 lands, ``sidequest.game.shared_world_delta`` does
    not exist; the import-side fail message tells Dev exactly what to add.
    """
    try:
        from sidequest.game.shared_world_delta import build_shared_world_delta
    except ImportError as exc:  # pragma: no cover — RED until story lands
        pytest.fail(
            f"sidequest.game.shared_world_delta.build_shared_world_delta "
            f"not importable: {exc}. Story 45-1 must add this module."
        )

    handler, sd, room = session_handler_factory(
        slug="test-mp-handshake-build",
        mode=GameMode.MULTIPLAYER,
        seat_players=[("p1", "Orin"), ("p2", "Blutka")],
        active_player=("p1", "Orin"),
    )
    room.snapshot.location = "rusted_atrium"

    delta = build_shared_world_delta(room.snapshot, room=room)
    delta_dict = (
        delta.model_dump() if hasattr(delta, "model_dump") else dict(delta)
    )

    assert delta_dict.get("location") == "rusted_atrium", (
        f"delta.location should reflect snapshot.location, got {delta_dict!r}"
    )
    # encounter_id key is mandatory even when None — consumers shouldn't
    # have to distinguish "missing" from "no encounter".
    assert "encounter_id" in delta_dict, (
        f"delta missing encounter_id key, got {delta_dict!r}"
    )
    formation = delta_dict.get("party_formation")
    assert formation is not None, (
        f"delta.party_formation missing, got {delta_dict!r}"
    )
    formation_pids = {entry.get("player_id") for entry in formation}
    assert {"p1", "p2"}.issubset(formation_pids), (
        f"party_formation must include every seated player, got {formation_pids!r}"
    )
    # Adjacency-relevant: every entry has a location.
    for entry in formation:
        assert entry.get("location"), (
            f"party_formation entry missing location: {entry!r}"
        )


def test_shared_world_delta_excludes_perceived_state(
    session_handler_factory,
) -> None:
    """Canonical/perceived split (ADR-037, SOUL.md): the delta MUST NOT
    carry character mood/personality/description. If perceived fields
    leak, the next player's POV state gets clobbered by the prior actor's
    perception — bigger break than the original "collapsed corridor" bug.

    Negative producer-side test: plant sentinel strings on each PC's
    perceived fields, build the delta, ensure no sentinel appears in
    its serialized form.
    """
    try:
        from sidequest.game.shared_world_delta import build_shared_world_delta
    except ImportError as exc:  # pragma: no cover — RED until story lands
        pytest.fail(
            f"build_shared_world_delta not importable: {exc}. "
            f"Story 45-1 must add sidequest.game.shared_world_delta."
        )

    handler, sd, room = session_handler_factory(
        slug="test-mp-handshake-perceived",
        mode=GameMode.MULTIPLAYER,
        seat_players=[("p1", "Orin"), ("p2", "Blutka")],
        active_player=("p1", "Orin"),
    )
    snapshot = room.snapshot
    for char in snapshot.characters:
        if char.core.name == "Orin":
            char.core.personality = "ORIN_PERCEIVED_MOOD_LEAK_SENTINEL"
            char.core.description = "ORIN_PERCEIVED_DESC_LEAK_SENTINEL"
        elif char.core.name == "Blutka":
            char.core.personality = "BLUTKA_PERCEIVED_MOOD_LEAK_SENTINEL"
            char.core.description = "BLUTKA_PERCEIVED_DESC_LEAK_SENTINEL"
    snapshot.location = "rusted_atrium"

    delta = build_shared_world_delta(snapshot, room=room)
    delta_dict = (
        delta.model_dump() if hasattr(delta, "model_dump") else dict(delta)
    )
    serialized = json.dumps(delta_dict)

    for sentinel in (
        "ORIN_PERCEIVED_MOOD_LEAK_SENTINEL",
        "ORIN_PERCEIVED_DESC_LEAK_SENTINEL",
        "BLUTKA_PERCEIVED_MOOD_LEAK_SENTINEL",
        "BLUTKA_PERCEIVED_DESC_LEAK_SENTINEL",
    ):
        assert sentinel not in serialized, (
            f"perceived field leaked into shared-world delta: {sentinel!r} "
            f"found in {serialized!r}. Canonical/perceived split (ADR-037) broken."
        )

    # Positive sanity: the canonical fields ARE present.
    assert delta_dict.get("location") == "rusted_atrium"
    assert "party_formation" in delta_dict


def test_merge_preserves_perceived_character_fields(
    session_handler_factory,
) -> None:
    """Consumer-side negative test: applying a delta to a snapshot must
    not mutate per-character perceived fields. If the merge clobbers
    ``character.core.personality``/``description``, POV state is leaking
    across saves.
    """
    try:
        from sidequest.game.shared_world_delta import (
            build_shared_world_delta,
            merge_shared_delta_into_snapshot,
        )
    except ImportError as exc:  # pragma: no cover — RED until story lands
        pytest.fail(
            f"shared_world_delta merge helpers not importable: {exc}. "
            f"Story 45-1 must add this module."
        )

    handler, sd, _room = session_handler_factory(
        slug="test-mp-merge-perceived",
        mode=GameMode.MULTIPLAYER,
        seat_players=[("p1", "Orin"), ("p2", "Blutka")],
        active_player=("p1", "Orin"),
    )
    snapshot = sd.snapshot
    for char in snapshot.characters:
        if char.core.name == "Blutka":
            char.core.personality = "BLUTKA_POV_PERSONALITY"
            char.core.description = "BLUTKA_POV_DESCRIPTION"

    delta = build_shared_world_delta(snapshot, room=None)
    snapshot.location = "rusted_atrium"

    result = merge_shared_delta_into_snapshot(snapshot, delta)
    blutka = next(c for c in snapshot.characters if c.core.name == "Blutka")
    assert blutka.core.personality == "BLUTKA_POV_PERSONALITY", (
        "merge clobbered Blutka's POV personality — perceived split broken"
    )
    assert blutka.core.description == "BLUTKA_POV_DESCRIPTION", (
        "merge clobbered Blutka's POV description — perceived split broken"
    )
    # The merge helper must signal what it did so callers can route
    # OTEL events. None means the helper returned nothing meaningful.
    assert result is not None, (
        "merge_shared_delta_into_snapshot must return a result that callers "
        "can use to populate OTEL attrs (delta_fields, conflict_count, "
        "resolution_path) — story 45-1 AC3"
    )


# ---------------------------------------------------------------------------
# AC2 — Boundary test: state_summary JSON the narrator sees exposes
# canonical party formation. This is THE wire-first boundary test for the
# apply side — _build_turn_context is what the orchestrator consumes.
# ---------------------------------------------------------------------------


def test_build_turn_context_state_summary_exposes_party_formation(
    session_handler_factory,
) -> None:
    """``_build_turn_context`` must expose canonical party formation in
    the JSON the narrator sees (``state_summary``). Without it the
    narrator fabricates separations (playtest 3: "collapsed corridor"
    between Orin and Blutka).

    Whatever shape Dev picks (top-level ``party_formation`` array,
    ``shared_world_delta`` envelope, or per-character ``location``),
    the contract is: every seated player_id appears with a location
    in the JSON the narrator sees.
    """
    from sidequest.server.session_helpers import _build_turn_context

    handler, sd, room = session_handler_factory(
        slug="test-mp-handshake-apply",
        mode=GameMode.MULTIPLAYER,
        seat_players=[("p1", "Orin"), ("p2", "Blutka")],
        active_player=("p2", "Blutka"),  # building B's context
    )
    room.snapshot.location = "rusted_atrium"

    ctx = _build_turn_context(sd, room=room)
    payload = json.loads(ctx.state_summary)

    serialized = json.dumps(payload)
    assert "rusted_atrium" in serialized, (
        "state_summary should expose the canonical room — Orin's narrator "
        "fabricated 'collapsed corridor' precisely because Blutka's location "
        "wasn't in this JSON"
    )
    assert "Orin" in serialized
    assert "Blutka" in serialized

    # Stronger contract: an explicit party_formation must be present so
    # the narrator gets a structured adjacency signal, not inferred-from-
    # characters[]. This is what 37-37 specified and what the playtest
    # evidence demands.
    formation = payload.get("party_formation") or payload.get(
        "shared_world_delta", {}
    ).get("party_formation")
    assert formation is not None, (
        "state_summary must carry an explicit party_formation array — "
        "narrator-inferred adjacency is what produced the 'collapsed corridor' "
        "fabrication in playtest 3"
    )
    formation_pids = {entry.get("player_id") for entry in formation}
    assert {"p1", "p2"}.issubset(formation_pids), (
        f"party_formation must include every seated player, got {formation_pids!r}"
    )
    for entry in formation:
        assert entry.get("location"), (
            f"party_formation entry missing location: {entry!r}"
        )


def test_build_turn_context_emits_handshake_watcher_event(
    session_handler_factory,
) -> None:
    """When ``_build_turn_context`` performs the merge, it MUST emit a
    ``game.handshake.delta_applied`` watcher event so the GM panel can
    verify the merge fired. Without the event the lie detector fails:
    Claude can re-fabricate "collapsed corridor" silently.
    """
    from sidequest.server.session_helpers import _build_turn_context

    handler, sd, room = session_handler_factory(
        slug="test-mp-handshake-otel",
        mode=GameMode.MULTIPLAYER,
        seat_players=[("p1", "Orin"), ("p2", "Blutka")],
        active_player=("p2", "Blutka"),
    )
    room.snapshot.location = "rusted_atrium"

    # Patch the watcher_publish at every reasonable site — Dev may emit
    # from session_helpers (apply seam) or the helper module itself.
    with patch(
        "sidequest.server.session_helpers._watcher_publish",
        create=True,
    ) as wp_helpers, patch(
        "sidequest.game.shared_world_delta._watcher_publish",
        create=True,
    ) as wp_module:
        _build_turn_context(sd, room=room)

    all_calls = list(wp_helpers.call_args_list) + list(wp_module.call_args_list)
    event_names = [call.args[0] for call in all_calls if call.args]
    assert "game.handshake.delta_applied" in event_names, (
        "GM panel needs the handshake event so it can verify the merge "
        f"actually fired. Saw events: {event_names!r}. Story 45-1 must "
        "emit _watcher_publish('game.handshake.delta_applied', ...) from "
        "the merge site."
    )
    # Schema check: required attributes must be present.
    matches = [
        call for call in all_calls
        if call.args and call.args[0] == "game.handshake.delta_applied"
    ]
    attrs = matches[0].args[1] if len(matches[0].args) > 1 else {}
    for required in ("delta_fields", "conflict_count", "resolution_path"):
        assert required in attrs, (
            f"watcher event missing '{required}' attribute, got {attrs!r}"
        )


# ---------------------------------------------------------------------------
# AC3 — OTEL span constant + SPAN_ROUTES registration.
# ---------------------------------------------------------------------------


def test_span_constant_and_route_registered() -> None:
    """The story registers a new OTEL span ``game.handshake.delta_applied``.
    Constant must exist and SPAN_ROUTES must carry an entry so the OTEL
    watcher dashboard renders it (CLAUDE.md observability principle —
    GM panel is the lie detector for fabricated narration).
    """
    from sidequest.telemetry import spans

    assert hasattr(spans, "SPAN_GAME_HANDSHAKE_DELTA_APPLIED"), (
        "missing SPAN_GAME_HANDSHAKE_DELTA_APPLIED constant in spans.py"
    )
    span_name = spans.SPAN_GAME_HANDSHAKE_DELTA_APPLIED
    assert span_name == "game.handshake.delta_applied", (
        f"span name should be 'game.handshake.delta_applied', got {span_name!r}"
    )
    assert span_name in spans.SPAN_ROUTES, (
        "SPAN_ROUTES is missing a registration for "
        "SPAN_GAME_HANDSHAKE_DELTA_APPLIED — completeness lint will fail"
    )
    route = spans.SPAN_ROUTES[span_name]
    assert route.component == "game", (
        f"route.component should be 'game', got {route.component!r}"
    )


# ---------------------------------------------------------------------------
# Wire-first project rule: every new pub export has at least one non-test
# consumer in the SAME PR diff. These source-grep tests pin the import
# surface so Reviewer can mechanically verify wiring.
# ---------------------------------------------------------------------------


def test_session_helpers_invokes_shared_world_delta() -> None:
    """``_build_turn_context`` is the apply-side seam (session_helpers).
    The merge helper must be referenced from there — failing this test
    catches the case where Dev defines the helper but never calls it
    from the production turn-build path.
    """
    from sidequest.server import session_helpers

    with open(session_helpers.__file__) as fh:
        source = fh.read()
    assert "shared_world_delta" in source, (
        "session_helpers.py does not reference shared_world_delta — the "
        "merge step is unwired even if the helper exists. wire-first gate fails."
    )


def test_session_handler_invokes_shared_world_delta() -> None:
    """``_execute_narration_turn`` (session_handler) is the emit-side
    seam. It must call into the delta builder so the broadcast carries
    a non-None state_delta. wire-first: dead exports are not allowed.
    """
    from sidequest.server import session_handler

    with open(session_handler.__file__) as fh:
        source = fh.read()
    assert "shared_world_delta" in source, (
        "session_handler.py does not reference shared_world_delta — the "
        "emit step is unwired. The NarrationEnd broadcast will keep sending "
        "state_delta=None. wire-first gate fails."
    )
    # Stronger check: the NarrationEndPayload(state_delta=None) line at
    # session_handler.py:3706 must be replaced by something non-None.
    # Find every NarrationEndPayload(...) call site and check that NONE of
    # them hardcode state_delta=None.
    # (A grep for the literal would catch any drift back to the bug.)
    assert "NarrationEndPayload(state_delta=None)" not in source, (
        "session_handler.py still hardcodes NarrationEndPayload(state_delta=None) — "
        "Story 45-1 must replace this with the shared-world delta payload."
    )
