"""Unit + wire tests for discovered_regions write-time validation
(Story 45-16).

Regression evidence: Playtest 3 Felix observed `(aside — narrator brief)`
registered as a traversable region alongside legitimate rooms. The
narrator-driven write paths (`narration_apply.location_update` and
`session.apply_world_state_patch.discover_regions`) appended entries
without inspecting their shape; this file pins the rejection rule.

Two layers of coverage:

1. **Unit** — `validate_region_name` returns the correct
   ``(is_valid, reason)`` tuple for every shape the playtest observed
   plus adjacent edge cases.
2. **Wire** — production write paths `_apply_narration_result_to_snapshot`
   and `GameSnapshot.apply_world_state_patch` route candidate names
   through the validator and emit ``region.entry_rejected`` on rejection.
   Without these, the validator could exist but be unreachable from
   non-test callers (CLAUDE.md "Verify wiring, not just existence").
"""

from __future__ import annotations

import pytest

from tests._helpers.session_room import room_for

# ---------------------------------------------------------------------------
# Layer 1 — Unit tests on validate_region_name
# ---------------------------------------------------------------------------


class TestValidatorAcceptsLegitimateRegions:
    """Names matching shapes seen in test fixtures and real worlds pass."""

    @pytest.mark.parametrize(
        "name",
        [
            "Tood's Dome",
            "The Bridge",
            "Bridge",
            "Scavenger Pit",
            "Felix's Workshop",
            "New Dungeon",
            "Ashgate Square",
            "Montmartre",
        ],
    )
    def test_legitimate_region_passes(self, name: str) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name(name)
        assert ok is True
        assert reason is None

    def test_unicode_region_passes(self) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name("Tood’s Dôme")
        assert ok is True
        assert reason is None


class TestValidatorRejectsParentheticalAsides:
    """The exact Playtest 3 Felix leak shape, plus the bracket family."""

    def test_playtest_3_felix_leak_rejected(self) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name("(aside — narrator brief)")
        assert ok is False
        assert reason == "bracketed"

    @pytest.mark.parametrize(
        "name",
        [
            "(aside)",
            "[narrator note]",
            "{system: rule applied}",
            "<aside>foo</aside>",
        ],
    )
    def test_bracket_prefix_rejected(self, name: str) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name(name)
        assert ok is False
        assert reason == "bracketed"

    def test_leading_whitespace_then_bracket_rejected(self) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name("   (aside)")
        assert ok is False
        assert reason == "bracketed"


class TestValidatorRejectsEmptyAndWhitespace:
    @pytest.mark.parametrize("name", ["", "   ", "\t", None])
    def test_empty_or_blank_rejected(self, name) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name(name)
        assert ok is False
        assert reason == "empty"


class TestValidatorRejectsMultilineAndOversize:
    def test_multiline_rejected(self) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name("First line\nSecond line")
        assert ok is False
        assert reason == "multiline"

    def test_carriage_return_rejected(self) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name("First\rSecond")
        assert ok is False
        assert reason == "multiline"

    def test_oversize_rejected(self) -> None:
        from sidequest.game.region_validation import validate_region_name

        long_name = "A" * 81
        ok, reason = validate_region_name(long_name)
        assert ok is False
        assert reason == "too_long"

    def test_eighty_char_name_passes(self) -> None:
        from sidequest.game.region_validation import validate_region_name

        ok, reason = validate_region_name("A" * 80)
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# Layer 2 — Span registration + routing
# ---------------------------------------------------------------------------


class TestSpanRegistration:
    def test_span_constant_defined(self) -> None:
        from sidequest.telemetry.spans import SPAN_REGION_ENTRY_REJECTED

        assert SPAN_REGION_ENTRY_REJECTED == "region.entry_rejected"

    def test_span_routed_to_state_transition_event(self) -> None:
        from sidequest.telemetry.spans import (
            SPAN_REGION_ENTRY_REJECTED,
            SPAN_ROUTES,
        )

        route = SPAN_ROUTES.get(SPAN_REGION_ENTRY_REJECTED)
        assert route is not None
        assert route.event_type == "state_transition"
        assert route.component == "region_state"

    def test_span_extract_carries_audit_fields(self) -> None:
        from sidequest.telemetry.spans import (
            SPAN_REGION_ENTRY_REJECTED,
            SPAN_ROUTES,
        )

        route = SPAN_ROUTES[SPAN_REGION_ENTRY_REJECTED]

        class FakeSpan:
            attributes = {
                "entry": "(aside — narrator brief)",
                "entry_type": "string",
                "reason": "bracketed",
                "caller_path": "narration_apply.location_update",
                "rejection_count": 1,
            }

        extracted = route.extract(FakeSpan())
        assert extracted == {
            "field": "discovered_regions",
            "op": "entry_rejected",
            "entry": "(aside — narrator brief)",
            "entry_type": "string",
            "reason": "bracketed",
            "caller_path": "narration_apply.location_update",
            "rejection_count": 1,
        }


# ---------------------------------------------------------------------------
# Layer 3 — Wire tests: production paths invoke the validator + emit span
# ---------------------------------------------------------------------------


def _make_minimal_snapshot():
    """Construct a GameSnapshot stub sufficient for the location-update branch.

    The production seam reads ``snapshot.location``, mutates
    ``snapshot.discovered_regions``, and reads ``snapshot.turn_manager.interaction``.
    No other branches of ``_apply_narration_result_to_snapshot`` are triggered
    when ``NarrationTurnResult`` carries only ``narration`` + ``location``.
    """
    from sidequest.game.session import GameSnapshot

    return GameSnapshot()


def _make_narration_result(*, narration: str, location: str | None):
    from sidequest.agents.orchestrator import NarrationTurnResult

    return NarrationTurnResult(narration=narration, location=location)


class TestNarrationApplyWiring:
    """``_apply_narration_result_to_snapshot`` must filter the location
    candidate before appending to ``discovered_regions`` (the Felix
    leak path)."""

    def test_legitimate_location_appended(self) -> None:
        from sidequest.server.narration_apply import (
            _apply_narration_result_to_snapshot,
        )

        snap = _make_minimal_snapshot()
        result = _make_narration_result(
            narration="Felix walks the rim of the dome.",
            location="Tood's Dome",
        )

        _apply_narration_result_to_snapshot(snap, result, player_name="Felix", room=room_for(snap))

        assert "Tood's Dome" in snap.discovered_regions

    def test_aside_leak_blocked(self, otel_capture) -> None:
        """Reproduces the Playtest 3 Felix bug: narrator emits
        ``(aside — narrator brief)`` as the location field. The leak
        must NOT land in ``discovered_regions``, and the rejection
        must surface as a ``region.entry_rejected`` span so the GM
        panel sees the filter fire (CLAUDE.md OTEL principle)."""
        from sidequest.server.narration_apply import (
            _apply_narration_result_to_snapshot,
        )

        snap = _make_minimal_snapshot()
        result = _make_narration_result(
            narration="…",
            location="(aside — narrator brief)",
        )

        _apply_narration_result_to_snapshot(snap, result, player_name="Felix", room=room_for(snap))

        assert "(aside — narrator brief)" not in snap.discovered_regions

        rejection_spans = [
            s for s in otel_capture.get_finished_spans()
            if s.name == "region.entry_rejected"
        ]
        assert len(rejection_spans) == 1, (
            "Validator must emit region.entry_rejected so Sebastien's "
            "lie-detector audience can see the filter fire."
        )
        attrs = dict(rejection_spans[0].attributes)
        assert attrs["entry"] == "(aside — narrator brief)"
        assert attrs["reason"] == "bracketed"
        assert attrs["caller_path"] == "narration_apply.location_update"


class TestSessionPatchWiring:
    """``GameSnapshot.apply_world_state_patch`` must filter both the
    wholesale-replace path (``patch.discovered_regions``) and the
    incremental-discover path (``patch.discover_regions``)."""

    def test_discover_regions_patch_filters_aside_leak(self, otel_capture) -> None:
        from sidequest.game.session import GameSnapshot, WorldStatePatch

        snap = GameSnapshot()
        patch = WorldStatePatch(
            discover_regions=["Felix's Workshop", "(aside — narrator brief)"]
        )

        snap.apply_world_patch(patch)

        assert "Felix's Workshop" in snap.discovered_regions
        assert "(aside — narrator brief)" not in snap.discovered_regions

        rejection_spans = [
            s for s in otel_capture.get_finished_spans()
            if s.name == "region.entry_rejected"
        ]
        assert len(rejection_spans) == 1
        attrs = dict(rejection_spans[0].attributes)
        assert attrs["caller_path"] == "session.apply_patch.discover_regions"
        assert attrs["reason"] == "bracketed"

    def test_discovered_regions_set_path_filters_aside_leak(self, otel_capture) -> None:
        """The wholesale-replace code path also routes through the
        validator; a patch wholesale-replacing the list with a leaked
        entry must be filtered, not adopted verbatim."""
        from sidequest.game.session import GameSnapshot, WorldStatePatch

        snap = GameSnapshot()
        patch = WorldStatePatch(
            discovered_regions=["Felix's Workshop", "(aside — narrator brief)"]
        )

        snap.apply_world_patch(patch)

        assert snap.discovered_regions == ["Felix's Workshop"]

        rejection_spans = [
            s for s in otel_capture.get_finished_spans()
            if s.name == "region.entry_rejected"
        ]
        assert len(rejection_spans) == 1
        attrs = dict(rejection_spans[0].attributes)
        assert attrs["caller_path"] == "session.apply_patch.discovered_regions_set"
