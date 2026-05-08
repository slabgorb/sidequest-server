"""Wiring tests for ``WebSocketSessionHandler._dispatch_pending_magic_frames``.

Story 47-3 round-2 mandatory follow-up #3 (Westley re-review): the
dispatcher introduced to close the round-1 wire-first gap had ZERO
direct unit tests. Existing coverage stops at the snapshot-stash side
(`test_resolution_stashes_outcome_payload_on_snapshot` asserts the
field is populated) — nothing exercised the actual dispatch step that
converts ``snapshot.pending_magic_*`` queues into outbound
``CONFRONTATION`` and ``CONFRONTATION_OUTCOME`` WebSocket frames via
``_emit_event``.

Per CLAUDE.md "Verify wiring, not just existence" + "Every test suite
needs a wiring test." A refactor that mistypes the kind string (e.g.
``CONFRONTATION_REVEAL`` instead of ``CONFRONTATION_OUTCOME``) breaks
the entire reveal pipeline AND passes every test in ``tests/`` because
the UI test simulates the message from the consumer side.

These tests pin the dispatcher's contract:
  - One CONFRONTATION frame is emitted per ``pending_magic_auto_fires`` entry
  - One CONFRONTATION_OUTCOME frame is emitted when ``pending_magic_confrontation_outcome`` is set
  - Both queue fields are reset after dispatch
  - Empty queues are a no-op
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sidequest.protocol.messages import (
    ConfrontationOutcomePayload,
    ConfrontationPayload,
)


def _valid_auto_fire_payload(
    *,
    confrontation_id: str = "the_bleeding_through",
    label: str = "The Bleeding-Through",
    actor: str = "sira_mendes",
    primary: str = "sanity",
    secondary: str = "vitality",
    genre_slug: str = "space_opera",
) -> dict:
    """Match the shape ``_build_magic_confrontation_payload`` produces."""
    return {
        "type": confrontation_id,
        "label": label,
        "category": "magic_confrontation",
        "actors": [{"name": actor, "role": "channeler"}],
        "player_metric": {
            "name": primary,
            "current": 4,
            "starting": 4,
            "threshold": 10,
        },
        "opponent_metric": {
            "name": secondary,
            "current": 5,
            "starting": 0,
            "threshold": 10,
        },
        "beats": [],
        "secondary_stats": None,
        "genre_slug": genre_slug,
        "mood": "haunted",
        "active": True,
    }


def _valid_outcome_payload(
    *,
    confrontation_id: str = "the_bleeding_through",
    label: str = "The Bleeding-Through",
    branch: str = "pyrrhic_win",
) -> dict:
    """Match the shape ``resolve_magic_confrontation`` returns."""
    return {
        "confrontation_id": confrontation_id,
        "label": label,
        "branch": branch,
        "mandatory_outputs": ["control_tier_advance", "status_add_scar"],
    }


def _make_handler_and_snapshot(tmp_path):
    """Construct a minimal WebSocketSessionHandler + GameSnapshot.

    Bypasses ``session_handler_factory`` to avoid the unrelated
    ``flickering_reach/openings.yaml`` test-fixture drift that breaks
    the genre loader during test-suite setup. The dispatcher under test
    only depends on ``self._emit_event`` (which we mock) and the
    snapshot's ``pending_magic_*`` fields — no genre pack required.
    """
    from sidequest.game.session import GameSnapshot
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

    handler = WebSocketSessionHandler(
        save_dir=tmp_path,
        genre_pack_search_paths=[],  # don't load any pack
    )
    snapshot = GameSnapshot(genre_slug="space_opera")
    return handler, snapshot


def test_dispatcher_emits_confrontation_frame_per_auto_fire(
    tmp_path,
) -> None:
    """Each ``pending_magic_auto_fires`` entry becomes one CONFRONTATION frame.

    Wires the production path: ``apply_magic_working`` populates the
    queue with one entry per auto-fire; the dispatcher drains it and
    emits one ``("CONFRONTATION", ConfrontationPayload)`` per entry via
    ``_emit_event``. Pre-fix (round 1): the dispatcher did not exist;
    auto_fired surfaced on the result but no production caller iterated
    it.
    """
    handler, snapshot = _make_handler_and_snapshot(tmp_path)

    # Spy: replace _emit_event with a MagicMock so we can inspect calls.
    # The real implementation persists to EventLog + fans out to peers;
    # here we only care that the dispatcher invokes it with the right
    # kind string and payload TYPE — the broadcast machinery is exercised
    # by ``test_confrontation_mp_broadcast.py``.
    handler._emit_event = MagicMock(return_value=None)

    snapshot.pending_magic_auto_fires = [_valid_auto_fire_payload()]
    snapshot.pending_magic_confrontation_outcome = None

    handler._dispatch_pending_magic_frames(snapshot)

    # Exactly one CONFRONTATION frame, payload is a ConfrontationPayload
    # (not a raw dict — the dispatcher constructs the typed model).
    assert handler._emit_event.call_count == 1, (
        f"expected exactly one _emit_event call for the single queued "
        f"auto-fire; got {handler._emit_event.call_count}"
    )
    kind, payload = handler._emit_event.call_args.args
    assert kind == "CONFRONTATION", (
        f"dispatcher must emit kind=CONFRONTATION for auto-fire entries; got kind={kind!r}"
    )
    assert isinstance(payload, ConfrontationPayload), (
        f"payload must be a typed ConfrontationPayload, not {type(payload).__name__}"
    )
    assert payload.type == "the_bleeding_through"
    assert payload.active is True
    assert payload.genre_slug == "space_opera"

    # Queue must be cleared after dispatch — otherwise the next
    # dispatcher tick re-fires the same frame.
    assert snapshot.pending_magic_auto_fires == [], (
        "dispatcher must clear pending_magic_auto_fires after emitting "
        "all entries; otherwise a second dispatch tick re-fires"
    )


def test_dispatcher_emits_outcome_frame_when_pending(
    tmp_path,
) -> None:
    """Pending outcome stash becomes one CONFRONTATION_OUTCOME frame.

    Wires the production path that previous Reviewer flagged as the
    headline gap: ``_resolve_magic_confrontation_if_applicable``
    populates ``snapshot.pending_magic_confrontation_outcome`` with the
    resolved branch + mandatory_outputs; the dispatcher emits it as a
    ``("CONFRONTATION_OUTCOME", ConfrontationOutcomePayload)`` frame.

    Pre-fix (round 1): the snapshot field did not exist; OTEL fired but
    no WebSocket frame ever reached the UI overlay.
    """
    handler, snapshot = _make_handler_and_snapshot(tmp_path)
    handler._emit_event = MagicMock(return_value=None)

    snapshot.pending_magic_auto_fires = []
    snapshot.pending_magic_confrontation_outcome = _valid_outcome_payload()

    handler._dispatch_pending_magic_frames(snapshot)

    assert handler._emit_event.call_count == 1
    kind, payload = handler._emit_event.call_args.args
    assert kind == "CONFRONTATION_OUTCOME", (
        f"dispatcher must emit kind=CONFRONTATION_OUTCOME for the "
        f"outcome payload; got kind={kind!r}"
    )
    assert isinstance(payload, ConfrontationOutcomePayload), (
        f"payload must be a typed ConfrontationOutcomePayload, not {type(payload).__name__}"
    )
    assert payload.confrontation_id == "the_bleeding_through"
    assert payload.label == "The Bleeding-Through"
    assert payload.branch == "pyrrhic_win"
    assert payload.mandatory_outputs == ["control_tier_advance", "status_add_scar"]

    # Field reset to None after dispatch so a second tick doesn't re-fire.
    assert snapshot.pending_magic_confrontation_outcome is None, (
        "dispatcher must reset pending_magic_confrontation_outcome to "
        "None after emitting; otherwise a second dispatch tick re-fires"
    )


def test_dispatcher_emits_both_frames_in_one_call(
    tmp_path,
) -> None:
    """Auto-fires AND outcome can be queued in the same turn — dispatcher emits both.

    A turn that crosses an auto-fire threshold AND resolves an
    encounter populates both queues. The dispatcher must drain both.
    Pre-fix: neither queue existed; this test would have nothing to
    drain.
    """
    handler, snapshot = _make_handler_and_snapshot(tmp_path)
    handler._emit_event = MagicMock(return_value=None)

    snapshot.pending_magic_auto_fires = [_valid_auto_fire_payload()]
    snapshot.pending_magic_confrontation_outcome = _valid_outcome_payload()

    handler._dispatch_pending_magic_frames(snapshot)

    assert handler._emit_event.call_count == 2, (
        f"expected 2 _emit_event calls (one CONFRONTATION + one "
        f"CONFRONTATION_OUTCOME); got {handler._emit_event.call_count}"
    )
    kinds = [c.args[0] for c in handler._emit_event.call_args_list]
    # CONFRONTATION fires first (auto-fire begins the encounter), then
    # CONFRONTATION_OUTCOME (resolution is a separate event).
    assert kinds == ["CONFRONTATION", "CONFRONTATION_OUTCOME"], (
        f"dispatch order matters — auto-fire first, outcome second; got {kinds}"
    )

    # Both queues cleared.
    assert snapshot.pending_magic_auto_fires == []
    assert snapshot.pending_magic_confrontation_outcome is None


def test_dispatcher_drains_multiple_auto_fires_in_one_call(
    tmp_path,
) -> None:
    """Two confrontations firing in one working → two CONFRONTATION frames.

    Address gap noted by reviewer-test-analyzer (medium): the
    multi-fire path was untested. ``apply_magic_working`` can append
    multiple entries when a single working crosses two thresholds
    simultaneously (e.g., sanity drops to 0.40 AND notice rises to
    0.75 in one shift). The dispatcher must emit one frame per entry.
    """
    handler, snapshot = _make_handler_and_snapshot(tmp_path)
    handler._emit_event = MagicMock(return_value=None)

    snapshot.pending_magic_auto_fires = [
        _valid_auto_fire_payload(
            confrontation_id="the_bleeding_through",
            label="The Bleeding-Through",
            primary="sanity",
            secondary="vitality",
        ),
        _valid_auto_fire_payload(
            confrontation_id="the_quiet_word",
            label="The Quiet Word",
            primary="notice",
            secondary="hegemony_heat",
        ),
    ]
    snapshot.pending_magic_confrontation_outcome = None

    handler._dispatch_pending_magic_frames(snapshot)

    assert handler._emit_event.call_count == 2, (
        f"two queued auto-fires must produce two CONFRONTATION frames; "
        f"got {handler._emit_event.call_count}"
    )
    types = [c.args[1].type for c in handler._emit_event.call_args_list]
    assert types == ["the_bleeding_through", "the_quiet_word"], (
        f"frames must preserve queue order; got {types}"
    )
    assert all(c.args[0] == "CONFRONTATION" for c in handler._emit_event.call_args_list)
    assert snapshot.pending_magic_auto_fires == []


def test_dispatcher_no_op_when_both_queues_empty(
    tmp_path,
) -> None:
    """Empty queues → no _emit_event calls. Cheap, called every turn."""
    handler, snapshot = _make_handler_and_snapshot(tmp_path)
    handler._emit_event = MagicMock(return_value=None)

    snapshot.pending_magic_auto_fires = []
    snapshot.pending_magic_confrontation_outcome = None

    handler._dispatch_pending_magic_frames(snapshot)

    assert handler._emit_event.call_count == 0, (
        "dispatcher must be a no-op when both queues are empty; "
        f"got {handler._emit_event.call_count} unexpected emit_event calls"
    )


def test_dispatcher_clears_queue_per_entry_to_survive_payload_error(
    tmp_path,
) -> None:
    """Round-2 medium finding: malformed payload poisons the queue forever.

    Pre-fix: ``_dispatch_pending_magic_frames`` clears the auto-fire
    queue ONLY after the for-loop completes. A ``ConfrontationPayload(**raw)``
    ValidationError on entry N leaves entries N..end stuck in the queue
    AND re-fires every previously-emitted entry on the next dispatch
    tick (since the clear assignment never executes).

    Post-fix expectation: the dispatcher must drain entries it
    successfully emits even if a later entry fails — pop-as-you-go OR
    per-entry try/except. This test pins the per-entry-clear contract.

    To trigger the failure, we queue one entry MISSING the required
    ``genre_slug`` field — pydantic raises ValidationError on construction.
    A queue with a valid entry followed by a bad one must end up with:
      - The valid entry emitted (one _emit_event call)
      - The bad entry remaining in the queue (or removed AND surfaced via
        watcher event — implementer's choice; either is loud per
        CLAUDE.md, both are acceptable post-fix shapes)
      - NOT both entries stuck because a later raise reverts the clear
    """
    handler, snapshot = _make_handler_and_snapshot(tmp_path)
    handler._emit_event = MagicMock(return_value=None)

    valid = _valid_auto_fire_payload()
    bad = _valid_auto_fire_payload()
    del bad["genre_slug"]  # ConfrontationPayload requires genre_slug

    snapshot.pending_magic_auto_fires = [valid, bad]
    snapshot.pending_magic_confrontation_outcome = None

    # Implementation choice for the green phase: either swallow the
    # ValidationError per entry (with a watcher event) and continue, or
    # pop entries as they're successfully emitted so the bad entry
    # doesn't take down the good one. EITHER way the valid entry must
    # have been emitted exactly once. Without the per-entry safety, the
    # raise propagates and _emit_event was called 1x but the queue
    # still contains BOTH entries (the post-loop clear never ran).
    raised_to_caller = False
    try:
        handler._dispatch_pending_magic_frames(snapshot)
    except Exception:
        raised_to_caller = True

    # The valid entry MUST have been emitted regardless of fix shape.
    # _emit_event was called at least once with the valid CONFRONTATION.
    assert handler._emit_event.call_count >= 1, (
        "the valid entry must be emitted before (or independently of) "
        "the failing entry; got 0 emit_event calls"
    )
    valid_calls = [
        c
        for c in handler._emit_event.call_args_list
        if c.args[0] == "CONFRONTATION" and c.args[1].type == "the_bleeding_through"
    ]
    assert len(valid_calls) >= 1

    # If the implementation swallows + reports, the queue should be
    # empty (or contain only the bad entry surfaced as a watcher event).
    # If the implementation propagates, the queue still has the valid
    # entry stuck — a second dispatch tick re-fires it. THAT is the
    # bug this test catches: the post-loop clear must not be the only
    # path that drains the queue.
    if not raised_to_caller:
        # Swallow-and-continue branch: valid entry must be drained.
        assert valid not in snapshot.pending_magic_auto_fires, (
            "successfully emitted entries must be removed from the queue "
            "even when later entries fail — otherwise a second dispatch "
            "tick re-fires every previously-emitted CONFRONTATION"
        )
    else:
        # Propagate-on-error branch: at minimum the valid entry must be
        # drained before the raise (pop-as-you-go), so the next dispatch
        # tick doesn't re-emit it.
        assert valid not in snapshot.pending_magic_auto_fires, (
            "the valid entry must be drained BEFORE the bad entry's "
            "raise — otherwise re-running dispatch re-fires the valid "
            "CONFRONTATION every tick"
        )
