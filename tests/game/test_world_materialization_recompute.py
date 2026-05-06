"""Story 45-19 — unit tests for the arc-recompute predicate and helper.

Felix's Playtest 3 (2026-04-19) reached turn 72 with a snapshot that was
still reporting ``campaign_maturity="Fresh"`` and four chapters covering
turns 1-30 only. The chargen-time materialization wrote once and no
caller ever invoked ``materialize_world`` again — so the bug is not in
the materialization formula itself but in the *cadence* at which it
fires.

These tests pin down the public API surface for the cadence:

- ``ARC_RECOMPUTE_INTERVAL`` — module-level constant that future tuning
  passes can adjust at one site.
- ``should_recompute_arc(interaction)`` — the predicate the dispatch
  loop calls to decide whether the just-completed turn is a tick turn.
- A recompute helper that wraps ``materialize_world`` and emits both
  the always-fire ``world_history.arc_tick`` span and the transition-
  only ``world_history.arc_promoted`` span.

The wire-level boundary tests (cadence firing from the actual dispatch
loop, attribute payload, idempotency past Veteran) live in
``tests/server/test_arc_recompute_wire.py`` — those exercise the seam
that wire-first requires. These unit tests guard the helper itself so
that a future caller (e.g. the catch-up replay path) gets the same
behaviour.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.history_chapter import HistoryChapter
from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.game.world_materialization import (
    ARC_RECOMPUTE_INTERVAL,
    CampaignMaturity,
    materialize_world,
    recompute_arc_history,
    should_recompute_arc,
)
from sidequest.telemetry.setup import init_tracer


@pytest.fixture
def otel_capture():
    """Install an in-memory span exporter on the current TracerProvider."""

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()
        exporter.clear()


def _three_tier_chapters() -> list[HistoryChapter]:
    """Synthetic chapter list covering Early / Mid / Veteran tiers.

    Each chapter carries a unique ``location`` so the tests can prove
    that ``materialize_world`` does (or does not) overwrite scene
    context. The ``fresh`` tier is intentionally absent — the fixture
    pack does not ship one and the recompute must still drive maturity
    transitions through the remaining tiers.
    """

    return [
        HistoryChapter(
            id="early",
            label="Early arc",
            location="Early Town",
            atmosphere="early-arc atmosphere",
            active_stakes="early stakes",
        ),
        HistoryChapter(
            id="mid",
            label="Mid arc",
            location="Mid Town",
            atmosphere="mid-arc atmosphere",
            active_stakes="mid stakes",
        ),
        HistoryChapter(
            id="veteran",
            label="Veteran arc",
            location="Veteran Town",
            atmosphere="veteran-arc atmosphere",
            active_stakes="veteran stakes",
        ),
    ]


def _snapshot_at(round: int, beats: int = 0) -> GameSnapshot:
    """Snapshot pinned to a specific (round, beats_fired) so the
    derived maturity is deterministic.
    """

    snap = GameSnapshot()
    tm = TurnManager()
    tm.round = round
    snap.turn_manager = tm
    snap.total_beats_fired = beats
    return snap


# ---------------------------------------------------------------------------
# ARC_RECOMPUTE_INTERVAL — module-level constant.
# ---------------------------------------------------------------------------


class TestArcRecomputeInterval:
    def test_constant_is_a_positive_int(self) -> None:
        """The interval must be a strictly-positive integer.

        Zero or negative would either fire every interaction (defeating
        the cadence) or never fire (regressing Felix's bug). The story's
        recommended value is 5; tests do not pin the exact number so a
        future tuning pass can adjust without churn.
        """

        assert isinstance(ARC_RECOMPUTE_INTERVAL, int)
        assert ARC_RECOMPUTE_INTERVAL > 0


# ---------------------------------------------------------------------------
# should_recompute_arc — the predicate the dispatch loop consults.
# ---------------------------------------------------------------------------


class TestShouldRecomputeArc:
    def test_fires_at_multiples_of_interval(self) -> None:
        n = ARC_RECOMPUTE_INTERVAL
        assert should_recompute_arc(n) is True
        assert should_recompute_arc(n * 2) is True
        assert should_recompute_arc(n * 7) is True

    def test_does_not_fire_off_cadence(self) -> None:
        n = ARC_RECOMPUTE_INTERVAL
        assert should_recompute_arc(1) is False
        assert should_recompute_arc(n - 1) is False
        assert should_recompute_arc(n + 1) is False

    def test_does_not_fire_at_zero(self) -> None:
        """Interaction 0 is the chargen materialization — the recompute
        path must not double-materialize the same turn.
        """

        assert should_recompute_arc(0) is False

    def test_negative_interaction_does_not_fire(self) -> None:
        """Defensive: a negative interaction is a programming bug, but
        the predicate should not treat it as a tick.
        """

        assert should_recompute_arc(-1) is False
        assert should_recompute_arc(-ARC_RECOMPUTE_INTERVAL) is False


# ---------------------------------------------------------------------------
# recompute_arc_history — the helper that wraps materialize_world and
# emits the always-fire arc_tick + transition-only arc_promoted spans.
# ---------------------------------------------------------------------------


class TestRecomputeArcHistory:
    def test_arc_tick_span_fires_on_every_call(self, otel_capture) -> None:
        """The tick span is the lie detector. It must fire on every
        recompute, regardless of whether the maturity tier changed.
        """

        snap = _snapshot_at(round=10)  # Early
        chapters = _three_tier_chapters()
        # Pre-materialize so the recompute is a tier-stable no-op.
        materialize_world(snap, chapters)

        otel_capture.clear()
        recompute_arc_history(snap, chapters)

        spans = otel_capture.get_finished_spans()
        ticks = [s for s in spans if s.name == "world_history.arc_tick"]
        assert len(ticks) == 1, (
            "arc_tick must fire on every recompute, even when tier is "
            f"stable. Spans seen: {[s.name for s in spans]}"
        )

    def test_arc_tick_attributes_populated(self, otel_capture) -> None:
        """Story 45-19 AC4: the tick span carries the full attribute set
        the GM panel surfaces — interaction, round, from/to maturity,
        chapter counts, tier_changed, cadence_interval.
        """

        snap = _snapshot_at(round=10)
        snap.turn_manager.interaction = 10
        chapters = _three_tier_chapters()

        otel_capture.clear()
        recompute_arc_history(snap, chapters)

        ticks = [s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_tick"]
        assert ticks
        attrs = ticks[0].attributes or {}
        for required in (
            "interaction",
            "round",
            "from_maturity",
            "to_maturity",
            "chapters_before",
            "chapters_after",
            "tier_changed",
            "cadence_interval",
        ):
            assert required in attrs, (
                f"arc_tick span missing required attribute {required!r}; got {sorted(attrs)}"
            )
        assert attrs["cadence_interval"] == ARC_RECOMPUTE_INTERVAL

    def test_arc_promoted_fires_only_on_tier_change(self, otel_capture) -> None:
        """``world_history.arc_promoted`` is a state-transition signal.
        The first recompute on a Fresh→Early snapshot must fire it; an
        immediate second recompute (same maturity) must not.
        """

        snap = _snapshot_at(round=10)  # Early
        chapters = _three_tier_chapters()

        otel_capture.clear()
        recompute_arc_history(snap, chapters)
        first_promoted = [
            s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_promoted"
        ]
        assert len(first_promoted) == 1, (
            "arc_promoted must fire on the first recompute that crosses "
            f"into Early. Spans: {[s.name for s in otel_capture.get_finished_spans()]}"
        )

        otel_capture.clear()
        recompute_arc_history(snap, chapters)
        second_promoted = [
            s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_promoted"
        ]
        assert second_promoted == [], "arc_promoted must NOT fire on a stable-maturity recompute"

    def test_arc_promoted_attributes(self, otel_capture) -> None:
        """The promoted span identifies the transition (from/to) and
        the new chapters that landed in this tick.
        """

        snap = _snapshot_at(round=10)
        chapters = _three_tier_chapters()

        otel_capture.clear()
        recompute_arc_history(snap, chapters)

        promoted = [
            s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_promoted"
        ]
        assert promoted
        attrs = promoted[0].attributes or {}
        for required in (
            "interaction",
            "from_maturity",
            "to_maturity",
            "chapters_added",
        ):
            assert required in attrs, f"arc_promoted span missing {required!r}; got {sorted(attrs)}"
        assert attrs["from_maturity"] == CampaignMaturity.Fresh.value
        assert attrs["to_maturity"] == CampaignMaturity.Early.value

    def test_idempotent_past_veteran(self, otel_capture) -> None:
        """Story 45-19 AC3: at turn 100 the maturity is Veteran (the
        top tier) and successive recomputes are no-ops. The arc_tick
        still fires (lie detector) but with ``tier_changed=False`` and
        identical chapter counts before/after.
        """

        snap = _snapshot_at(round=100)
        chapters = _three_tier_chapters()
        materialize_world(snap, chapters)
        chapter_count_pre = len(snap.world_history)

        otel_capture.clear()
        recompute_arc_history(snap, chapters)

        ticks = [s for s in otel_capture.get_finished_spans() if s.name == "world_history.arc_tick"]
        assert ticks
        attrs = ticks[0].attributes or {}
        assert attrs.get("tier_changed") is False, (
            "Past-Veteran recompute must not report a tier change"
        )
        assert attrs.get("chapters_before") == chapter_count_pre
        assert attrs.get("chapters_after") == chapter_count_pre
        assert len(snap.world_history) == chapter_count_pre

    def test_does_not_clobber_live_scene_fields(self) -> None:
        """Story 45-19 AC6: ``materialize_world`` only writes
        ``world_history`` and ``campaign_maturity``. Live scene context
        fields (``location`` / ``atmosphere`` / ``active_stakes``) that
        the dispatch loop has been mutating mid-session must survive
        the recompute untouched — otherwise every tick would snap the
        scene back to whatever the latest chapter declared.
        """

        from sidequest.game.character import Character
        from sidequest.game.creature_core import CreatureCore, Inventory

        snap = _snapshot_at(round=10)
        snap.characters.append(
            Character(
                core=CreatureCore(
                    name="Live PC",
                    description="x",
                    personality="x",
                    inventory=Inventory(),
                ),
                char_class="Adventurer",
                race="Human",
                backstory="x",
            )
        )
        chapters = _three_tier_chapters()
        # First recompute lands the chapter set so subsequent calls are
        # stable-maturity ticks.
        materialize_world(snap, chapters)

        # Player has moved on since chargen — live state has diverged
        # from anything the chapter declares (Wave 2B: per-character
        # location replaces the legacy party-level field).
        snap.character_locations["Live PC"] = "Live Player Location"
        snap.atmosphere = "live atmosphere narrated mid-session"
        snap.active_stakes = "live stakes the narrator just authored"

        recompute_arc_history(snap, chapters)

        assert snap.character_locations.get("Live PC") == "Live Player Location"
        assert snap.atmosphere == "live atmosphere narrated mid-session"
        assert snap.active_stakes == "live stakes the narrator just authored"
