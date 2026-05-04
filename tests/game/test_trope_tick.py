"""Story 45-27 — unit tests for the trope progression tick engine.

The wire-first boundary counterpart drives ``_execute_narration_turn``
end-to-end (tests/server/test_45_27_trope_tempo_wire.py); these tests
pin the tick engine's predicates so a refactor of the dispatch path
cannot silently weaken the cap, the cooldown, the stagger, or the rate
multiplier. Each test imports the new symbols from the to-be-written
modules — RED until 45-27's GREEN phase introduces them.

Five tuning dimensions land here, each its own predicate:

1. **Per-tick rate multiplier** — passive ``rate_per_turn`` is scaled
   down by ``PROGRESSION_RATE_MULTIPLIER`` (default 0.5). Half the
   playtest-3 pile-up was simply too-fast progression.
2. **Simultaneous-active cap** — at most
   ``MAX_SIMULTANEOUS_ACTIVE`` (default 3) progressing tropes; the
   (N+1)th candidate stays dormant and emits ``trope.cap_blocked``.
3. **Stagger** — when two progressing tropes both cross beat
   thresholds on the same tick, only the highest-progress trope
   actually fires. The other holds at the threshold and fires next
   eligible turn (after cooldown).
4. **Fire cooldown** — once any trope fires a beat (or resolves),
   no NEW dormant→progressing transition is allowed for
   ``FIRE_COOLDOWN_TURNS`` (default 2) turns. Already-progressing
   tropes continue advancing — cooldown only gates new activations.
5. **Foreground/background split helper** — ``select_foreground_tropes``
   returns the K most-active progressing tropes (Early zone) and the
   remainder for the Valley summary. Both fields go ``None`` on an
   empty world (zero-byte-leak per the prompt-section discipline used
   throughout orchestrator.py).

The constants live in a single module so playtest tuning has one place
to land — per CLAUDE.md "no silent fallbacks" + ADR-068 (magic literal
extraction). Tests reference them by import so they remain correct
under future tuning.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.session import GameSnapshot, TropeState
from sidequest.genre.models.tropes import (
    PassiveProgression,
    TropeDefinition,
    TropeEscalation,
)
from sidequest.telemetry.setup import init_tracer

# ---------------------------------------------------------------------------
# Helpers — fixture pack with tunable rates so these tests do not depend on
# the caverns_and_claudes content authoring choices.
# ---------------------------------------------------------------------------


def _trope_def(
    trope_id: str,
    *,
    rate_per_turn: float = 0.10,
    thresholds: tuple[float, ...] = (0.25, 0.50, 0.75, 1.00),
) -> TropeDefinition:
    """Build a deterministic TropeDefinition with a known rate + escalation."""

    return TropeDefinition(
        id=trope_id,
        name=trope_id.replace("_", " ").title(),
        category="tension",
        triggers=[],
        narrative_hints=[],
        escalation=[TropeEscalation(at=t, event=f"beat at {t}", stakes="") for t in thresholds],
        passive_progression=PassiveProgression(rate_per_turn=rate_per_turn),
    )


def _pack_with(tropes: list[TropeDefinition]):
    """Minimal stand-in for the GenrePack that ``tick_tropes`` reads.

    The tick consumes only ``pack.tropes``. We avoid a full GenrePack
    constructor (50+ required fields) by handing a SimpleNamespace with
    that single attribute — this matches the duck-typing pattern the
    Rust port preserved when building isolated test fixtures.
    """

    from types import SimpleNamespace

    return SimpleNamespace(tropes=tropes)


def _seed_snapshot(states: list[tuple[str, str, float]]) -> GameSnapshot:
    """Build a snapshot with ``active_tropes = [(id, status, progress), ...]``.

    Tropes are seeded in ``active_tropes`` so the tick observes them as
    candidates. ``turn_manager.interaction`` is set to a known value so
    cooldown bookkeeping is testable without round-counter arithmetic.
    """

    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    for trope_id, status, progress in states:
        snap.active_tropes.append(
            TropeState(id=trope_id, status=status, progress=progress, beats_fired=0)
        )
    snap.turn_manager.interaction = 10
    return snap


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


# ---------------------------------------------------------------------------
# Tuning constants — single source of truth, one module per ADR-068.
# ---------------------------------------------------------------------------


class TestTropeTuningConstants:
    """The four playtest-tunable knobs live in one module so a single
    PR can adjust them without grepping the codebase. Story context
    suggests sane initial values — tests pin the *shape* (type, range)
    rather than the exact value, so playtest can shift them without
    breaking the test suite.
    """

    def test_constants_module_exports_four_knobs(self) -> None:
        from sidequest.game import trope_tuning

        # Each constant must exist and be a primitive numeric. Missing
        # any one of them means the engine falls back to inline literals
        # — exactly the silent-fallback the story prohibits.
        assert isinstance(trope_tuning.MAX_SIMULTANEOUS_ACTIVE, int)
        assert isinstance(trope_tuning.FIRE_COOLDOWN_TURNS, int)
        assert isinstance(trope_tuning.FOREGROUND_K, int)
        assert isinstance(trope_tuning.PROGRESSION_RATE_MULTIPLIER, float)

    def test_max_simultaneous_active_in_playtest_band(self) -> None:
        """Description says "2-3"; below 2 collapses Lane B telemetry
        signal (only one trope ever active), above 3 reproduces the
        Felix pile-up. The band is the contract.
        """

        from sidequest.game.trope_tuning import MAX_SIMULTANEOUS_ACTIVE

        assert 2 <= MAX_SIMULTANEOUS_ACTIVE <= 3, (
            f"MAX_SIMULTANEOUS_ACTIVE={MAX_SIMULTANEOUS_ACTIVE} outside "
            "the description's 2-3 band; pile-up returns above 3, telemetry "
            "starves below 2."
        )

    def test_fire_cooldown_turns_is_positive(self) -> None:
        from sidequest.game.trope_tuning import FIRE_COOLDOWN_TURNS

        assert FIRE_COOLDOWN_TURNS >= 1, (
            f"FIRE_COOLDOWN_TURNS={FIRE_COOLDOWN_TURNS}: cooldown of 0 "
            "means back-to-back beats can fire — exactly the playtest-3 "
            "thrash the story is closing."
        )

    def test_foreground_k_does_not_exceed_cap(self) -> None:
        """Early zone shows the K most-active progressing tropes; if K
        exceeds the cap the prompt would carry phantom slots that no
        tick can ever fill.
        """

        from sidequest.game.trope_tuning import (
            FOREGROUND_K,
            MAX_SIMULTANEOUS_ACTIVE,
        )

        assert 1 <= FOREGROUND_K <= MAX_SIMULTANEOUS_ACTIVE, (
            f"FOREGROUND_K={FOREGROUND_K} not within "
            f"[1, MAX_SIMULTANEOUS_ACTIVE={MAX_SIMULTANEOUS_ACTIVE}]"
        )

    def test_progression_rate_multiplier_is_a_brake(self) -> None:
        """The multiplier slows down YAML-declared rates; values >1.0
        would *speed up* progression and reproduce the pile-up."""

        from sidequest.game.trope_tuning import PROGRESSION_RATE_MULTIPLIER

        assert 0.0 < PROGRESSION_RATE_MULTIPLIER <= 1.0, (
            f"PROGRESSION_RATE_MULTIPLIER={PROGRESSION_RATE_MULTIPLIER}: "
            "must be a brake (0,1]; values >1.0 accelerate progression."
        )


# ---------------------------------------------------------------------------
# TropeState extension — cooldown bookkeeping on the model.
# ---------------------------------------------------------------------------


class TestTropeStateBookkeepingFields:
    """Story 45-27 extends ``TropeState`` with two cooldown fields.
    ``model_config = {"extra": "ignore"}`` (already set) means old saves
    without these fields load with default values — forward-compat for
    legacy save reload (per project memory: legacy saves are throwaway,
    but the schema must still load).
    """

    def test_trope_state_has_fire_cooldown_until_field(self) -> None:
        """``fire_cooldown_until`` is the absolute interaction count at
        which a NEW activation is again allowed. Unset (None) means no
        active cooldown — distinguishable from interaction 0.
        """

        ts = TropeState(id="x", status="dormant", progress=0.0)
        # Field must exist with a typed default. Probing via getattr
        # so the test asserts the schema, not the default value.
        assert hasattr(ts, "fire_cooldown_until"), (
            "TropeState missing fire_cooldown_until — cooldown bookkeeping "
            "lives on the trope, not in a sidecar dict, so save/reload "
            "round-trips it for free."
        )
        # Default must be a sentinel (None or 0) so a fresh trope is not
        # in cooldown by default. Either is acceptable; the contract is
        # "no cooldown pre-fire".
        assert ts.fire_cooldown_until in (None, 0), (
            f"Fresh TropeState should not be in cooldown; "
            f"fire_cooldown_until={ts.fire_cooldown_until!r}"
        )

    def test_trope_state_has_last_fired_turn_field(self) -> None:
        """``last_fired_turn`` lets the GM panel chart "turns since last
        beat fire" alongside ``progress``. Story description requires
        ``progression_max`` and ``progression_avg`` per turn; this is
        the per-trope counterpart.
        """

        ts = TropeState(id="x", status="dormant", progress=0.0)
        assert hasattr(ts, "last_fired_turn"), (
            "TropeState missing last_fired_turn — without it, the GM "
            "panel cannot anchor cooldown remaining in turns."
        )
        assert ts.last_fired_turn is None, (
            f"Fresh TropeState should have last_fired_turn=None; got {ts.last_fired_turn!r}"
        )

    def test_trope_state_round_trips_through_pydantic(self) -> None:
        """Forward-compat: an old save without the new fields must load.
        ``extra='ignore'`` already on the model handles unknown fields;
        the new fields must default cleanly when absent.
        """

        legacy_payload = {
            "id": "extraction_panic",
            "status": "progressing",
            "progress": 0.4,
            "beats_fired": 1,
        }
        ts = TropeState.model_validate(legacy_payload)
        # Loaded without the new fields — defaults must apply.
        assert ts.fire_cooldown_until in (None, 0)
        assert ts.last_fired_turn is None


# ---------------------------------------------------------------------------
# Span constants — the new constants for activation refusals.
# ---------------------------------------------------------------------------


class TestNewSpanConstants:
    """``trope.cap_blocked`` and ``trope.cooldown_blocked`` are the
    diagnostic spans that surface "the engine refused to activate this"
    on the GM panel. Without these the panel cannot distinguish "no
    candidates this turn" from "the cap blocked one" — Sebastien's
    lie-detector cannot tell silent from suppressed.
    """

    def test_span_cap_blocked_constant_exists(self) -> None:
        from sidequest.telemetry.spans import SPAN_TROPE_CAP_BLOCKED

        assert SPAN_TROPE_CAP_BLOCKED == "trope.cap_blocked", (
            f"SPAN_TROPE_CAP_BLOCKED={SPAN_TROPE_CAP_BLOCKED!r}: span "
            "name must follow <subsystem>.<action> convention from epic "
            "context (CLAUDE.md OTEL principle)."
        )

    def test_span_cooldown_blocked_constant_exists(self) -> None:
        from sidequest.telemetry.spans import SPAN_TROPE_COOLDOWN_BLOCKED

        assert SPAN_TROPE_COOLDOWN_BLOCKED == "trope.cooldown_blocked"


# ---------------------------------------------------------------------------
# tick_tropes — the engine. Cap, cooldown, stagger, rate multiplier.
# ---------------------------------------------------------------------------


class TestTickRateMultiplier:
    """Per-tick progression rate is multiplied by the global brake.
    Knob: ``PROGRESSION_RATE_MULTIPLIER`` (default 0.5).
    """

    def test_progressing_trope_advances_at_scaled_rate(self) -> None:
        from sidequest.game.trope_tick import tick_tropes
        from sidequest.game.trope_tuning import PROGRESSION_RATE_MULTIPLIER

        snap = _seed_snapshot([("the_keeper_stirs", "progressing", 0.10)])
        pack = _pack_with([_trope_def("the_keeper_stirs", rate_per_turn=0.10)])

        tick_tropes(snap, pack, now_turn=11)

        keeper = next(t for t in snap.active_tropes if t.id == "the_keeper_stirs")
        # Δ = rate_per_turn (0.10) * multiplier (default 0.5) = 0.05.
        # Computed in-test rather than hardcoded so future tuning of the
        # multiplier does not require editing this assertion.
        expected = 0.10 + 0.10 * PROGRESSION_RATE_MULTIPLIER
        assert keeper.progress == pytest.approx(expected, abs=1e-6), (
            f"progress={keeper.progress} expected≈{expected}; "
            "rate multiplier did not scale the per-turn delta."
        )

    def test_dormant_trope_does_not_advance(self) -> None:
        """Dormant tropes must not accumulate progress passively. If
        they did, a cap-blocked trope would silently catch up while
        it sat in dormant — the cap would be cosmetic.
        """

        from sidequest.game.trope_tick import tick_tropes

        snap = _seed_snapshot([("the_deeper_dark", "dormant", 0.05)])
        pack = _pack_with([_trope_def("the_deeper_dark", rate_per_turn=0.10)])

        tick_tropes(snap, pack, now_turn=11)

        deeper = next(t for t in snap.active_tropes if t.id == "the_deeper_dark")
        assert deeper.progress == pytest.approx(0.05, abs=1e-6), (
            f"Dormant trope advanced from 0.05 to {deeper.progress} — "
            "dormant must be inert until the activation gate passes."
        )

    def test_resolved_trope_does_not_advance(self) -> None:
        """A resolved trope is terminal. Continuing to tick it would
        let progress climb past 1.0 and re-trigger the >=1.0 beat each
        turn — exactly the runaway 45-19 was about, in trope flavor.
        """

        from sidequest.game.trope_tick import tick_tropes

        snap = _seed_snapshot([("hireling_mutiny", "resolved", 1.00)])
        pack = _pack_with([_trope_def("hireling_mutiny", rate_per_turn=0.10)])

        tick_tropes(snap, pack, now_turn=11)

        mutiny = next(t for t in snap.active_tropes if t.id == "hireling_mutiny")
        assert mutiny.progress == pytest.approx(1.00, abs=1e-6)
        assert mutiny.status == "resolved"


class TestTickSimultaneousActiveCap:
    """At most ``MAX_SIMULTANEOUS_ACTIVE`` tropes may be progressing
    at the same time. The (N+1)th candidate stays dormant and emits
    ``trope.cap_blocked``.
    """

    def test_cap_blocks_extra_activation(self) -> None:
        from sidequest.game.trope_tick import tick_tropes
        from sidequest.game.trope_tuning import MAX_SIMULTANEOUS_ACTIVE

        # Seed cap+1 dormants whose progress already crossed the
        # activation gate (story leaves the gate predicate to the
        # implementer; tests use pre-crossed progress as a content-
        # agnostic activation trigger).
        cap = MAX_SIMULTANEOUS_ACTIVE
        n = cap + 1
        snap = _seed_snapshot([(f"t{i}", "dormant", 0.30) for i in range(n)])
        pack = _pack_with([_trope_def(f"t{i}", rate_per_turn=0.10) for i in range(n)])

        tick_tropes(snap, pack, now_turn=11)

        progressing = [t for t in snap.active_tropes if t.status == "progressing"]
        dormant = [t for t in snap.active_tropes if t.status == "dormant"]
        assert len(progressing) == cap, (
            f"Cap is {cap} but {len(progressing)} tropes ended up "
            f"progressing — pile-up returned. statuses="
            f"{[(t.id, t.status) for t in snap.active_tropes]}"
        )
        assert len(dormant) == 1, (
            f"Exactly 1 candidate must be queued (dormant); got "
            f"{len(dormant)} — see {[(t.id, t.status) for t in snap.active_tropes]}"
        )

    def test_cap_blocked_emits_span(self, otel_capture) -> None:
        """The blocked trope must show up on the GM panel; without the
        span Sebastien cannot distinguish "engine refused to activate"
        from "engine never ran".
        """

        from sidequest.game.trope_tick import tick_tropes
        from sidequest.game.trope_tuning import MAX_SIMULTANEOUS_ACTIVE

        cap = MAX_SIMULTANEOUS_ACTIVE
        snap = _seed_snapshot([(f"t{i}", "dormant", 0.30) for i in range(cap + 1)])
        pack = _pack_with([_trope_def(f"t{i}", rate_per_turn=0.10) for i in range(cap + 1)])

        otel_capture.clear()
        tick_tropes(snap, pack, now_turn=11)

        cap_blocked = [
            s for s in otel_capture.get_finished_spans() if s.name == "trope.cap_blocked"
        ]
        assert len(cap_blocked) == 1, (
            f"Expected exactly 1 trope.cap_blocked span; got "
            f"{[s.name for s in otel_capture.get_finished_spans()]}"
        )
        attrs = dict(cap_blocked[0].attributes or {})
        assert "trope_id" in attrs, "cap_blocked span missing trope_id"
        assert attrs.get("current_active_count") == cap, (
            f"current_active_count must equal cap; "
            f"attrs.current_active_count={attrs.get('current_active_count')}"
        )
        assert attrs.get("cap") == cap, f"cap attribute mismatch; attrs.cap={attrs.get('cap')}"

    def test_cap_does_not_demote_already_progressing_tropes(self) -> None:
        """If cap+1 tropes are *already* progressing on entry (loaded
        from a save where the engine wasn't yet enforcing the cap),
        the tick must NOT demote one — the cap gates new activations,
        not retroactively. Forward-compat with legacy saves.
        """

        from sidequest.game.trope_tick import tick_tropes
        from sidequest.game.trope_tuning import MAX_SIMULTANEOUS_ACTIVE

        cap = MAX_SIMULTANEOUS_ACTIVE
        # cap+1 already progressing at entry.
        snap = _seed_snapshot([(f"t{i}", "progressing", 0.20) for i in range(cap + 1)])
        pack = _pack_with([_trope_def(f"t{i}", rate_per_turn=0.10) for i in range(cap + 1)])

        tick_tropes(snap, pack, now_turn=11)

        progressing = [t for t in snap.active_tropes if t.status == "progressing"]
        assert len(progressing) == cap + 1, (
            "Cap must not demote already-progressing tropes; that would "
            "drop a thread mid-arc on first post-upgrade tick. Got "
            f"{[(t.id, t.status) for t in snap.active_tropes]}"
        )


class TestTickFireCooldown:
    """After any beat fires (or trope resolves), no NEW dormant→progressing
    transition is allowed for ``FIRE_COOLDOWN_TURNS`` turns. Already-
    progressing tropes continue to advance — cooldown only gates new
    activations.
    """

    def test_cooldown_blocks_new_activation(self) -> None:
        from sidequest.game.trope_tick import tick_tropes
        from sidequest.game.trope_tuning import FIRE_COOLDOWN_TURNS

        # One already-progressing trope at threshold (will fire on tick
        # 11) — its fire kicks the global cooldown. A separate dormant
        # candidate is ready to activate but must be held off for the
        # cooldown window.
        snap = _seed_snapshot(
            [
                ("the_keeper_stirs", "progressing", 0.21),
                ("hireling_mutiny", "dormant", 0.30),
            ]
        )
        pack = _pack_with(
            [
                _trope_def("the_keeper_stirs", rate_per_turn=0.10),
                _trope_def("hireling_mutiny", rate_per_turn=0.10),
            ]
        )

        # Tick 11: keeper crosses 0.25 threshold → fires → cooldown
        # starts. hireling_mutiny is candidate; cooldown blocks.
        tick_tropes(snap, pack, now_turn=11)

        keeper = next(t for t in snap.active_tropes if t.id == "the_keeper_stirs")
        mutiny = next(t for t in snap.active_tropes if t.id == "hireling_mutiny")
        assert keeper.beats_fired >= 1, (
            "Keeper should have fired at least one beat on this tick "
            f"(progress={keeper.progress}, beats_fired={keeper.beats_fired})"
        )
        assert mutiny.status == "dormant", (
            "Cooldown must block dormant→progressing while the cooldown "
            f"window is open; mutiny.status={mutiny.status!r}"
        )

        # Tick through the cooldown window. Each subsequent tick within
        # the window: hireling_mutiny stays dormant.
        for offset in range(1, FIRE_COOLDOWN_TURNS + 1):
            tick_tropes(snap, pack, now_turn=11 + offset)
            mutiny = next(t for t in snap.active_tropes if t.id == "hireling_mutiny")
            assert mutiny.status == "dormant", (
                f"Cooldown window violated at offset={offset}; "
                f"mutiny.status={mutiny.status!r} (FIRE_COOLDOWN_TURNS="
                f"{FIRE_COOLDOWN_TURNS})"
            )

        # Beyond the cooldown window: activation allowed again.
        tick_tropes(snap, pack, now_turn=11 + FIRE_COOLDOWN_TURNS + 1)
        mutiny = next(t for t in snap.active_tropes if t.id == "hireling_mutiny")
        assert mutiny.status == "progressing", (
            f"After cooldown expires (turn {11 + FIRE_COOLDOWN_TURNS + 1}) "
            f"hireling_mutiny should activate; got status={mutiny.status!r}"
        )

    def test_cooldown_does_not_freeze_existing_progress(self) -> None:
        """Negative test: an already-progressing trope must continue to
        advance during cooldown. Cooldown is a *new-activation* gate,
        not a global pause.
        """

        from sidequest.game.trope_tick import tick_tropes
        from sidequest.game.trope_tuning import PROGRESSION_RATE_MULTIPLIER

        # Two progressing tropes; first crosses a beat threshold (fires
        # → cooldown). Second must keep advancing.
        snap = _seed_snapshot(
            [
                ("the_keeper_stirs", "progressing", 0.21),
                ("hireling_mutiny", "progressing", 0.10),
            ]
        )
        pack = _pack_with(
            [
                _trope_def("the_keeper_stirs", rate_per_turn=0.10),
                _trope_def("hireling_mutiny", rate_per_turn=0.10),
            ]
        )

        tick_tropes(snap, pack, now_turn=11)

        mutiny = next(t for t in snap.active_tropes if t.id == "hireling_mutiny")
        expected = 0.10 + 0.10 * PROGRESSION_RATE_MULTIPLIER
        assert mutiny.progress == pytest.approx(expected, abs=1e-6), (
            f"Already-progressing trope must continue advancing during "
            f"cooldown; got progress={mutiny.progress}, expected≈{expected}"
        )

    def test_cooldown_blocked_emits_span(self, otel_capture) -> None:
        """Diagnostic span on every refusal so the GM panel can chart
        cooldown engagement, not just outcomes.
        """

        from sidequest.game.trope_tick import tick_tropes

        snap = _seed_snapshot(
            [
                ("the_keeper_stirs", "progressing", 0.21),
                ("hireling_mutiny", "dormant", 0.30),
            ]
        )
        pack = _pack_with(
            [
                _trope_def("the_keeper_stirs", rate_per_turn=0.10),
                _trope_def("hireling_mutiny", rate_per_turn=0.10),
            ]
        )

        # Tick 11 fires keeper, kicks cooldown. Tick 12: mutiny is
        # blocked by cooldown — that's the span we want to observe.
        tick_tropes(snap, pack, now_turn=11)
        otel_capture.clear()
        tick_tropes(snap, pack, now_turn=12)

        cooldown_blocked = [
            s for s in otel_capture.get_finished_spans() if s.name == "trope.cooldown_blocked"
        ]
        assert cooldown_blocked, (
            "trope.cooldown_blocked span did not fire on a cooldown-"
            "blocked activation. Spans: "
            f"{[s.name for s in otel_capture.get_finished_spans()]}"
        )
        attrs = dict(cooldown_blocked[0].attributes or {})
        assert attrs.get("trope_id") == "hireling_mutiny", (
            f"trope_id mismatch on cooldown_blocked span; attrs={attrs}"
        )
        assert attrs.get("current_turn") == 12
        assert isinstance(attrs.get("cooldown_until_turn"), int)


class TestTickStagger:
    """When two progressing tropes both cross beat thresholds on the
    same tick, only the highest-progress one fires. The other holds
    its progress at the threshold and fires on the next eligible turn
    (after cooldown).
    """

    def test_only_one_beat_fires_per_tick(self) -> None:
        from sidequest.game.trope_tick import tick_tropes

        # Two tropes both poised to cross their first threshold (0.25)
        # in the same tick. The keeper has slightly more progress so
        # it wins the stagger.
        snap = _seed_snapshot(
            [
                ("the_keeper_stirs", "progressing", 0.22),
                ("extraction_panic", "progressing", 0.20),
            ]
        )
        pack = _pack_with(
            [
                _trope_def("the_keeper_stirs", rate_per_turn=0.10),
                _trope_def("extraction_panic", rate_per_turn=0.10),
            ]
        )

        tick_tropes(snap, pack, now_turn=11)

        keeper = next(t for t in snap.active_tropes if t.id == "the_keeper_stirs")
        panic = next(t for t in snap.active_tropes if t.id == "extraction_panic")

        # The winner fires its beat.
        assert keeper.beats_fired == 1, (
            f"Higher-progress trope must fire; keeper.beats_fired="
            f"{keeper.beats_fired}, progress={keeper.progress}"
        )
        # The loser must NOT fire — its beats_fired stays at 0 and its
        # progress is held at the threshold (0.25) so it fires next
        # eligible turn rather than overshooting silently.
        assert panic.beats_fired == 0, (
            f"Lower-progress trope must NOT fire on same tick; "
            f"panic.beats_fired={panic.beats_fired} — stagger violated."
        )


class TestForegroundBackgroundSplit:
    """``select_foreground_tropes`` returns the K most-active progressing
    tropes (Early zone) and the remainder for Valley summary. The split
    is what lets the narrator know which thread is "now" vs background.
    """

    def test_split_returns_top_k_by_progress(self) -> None:
        from sidequest.game.trope_tick import select_foreground_tropes
        from sidequest.game.trope_tuning import FOREGROUND_K

        # 3 progressing + 1 dormant. The split must take only progressing
        # and rank by progress (descending).
        snap = _seed_snapshot(
            [
                ("low", "progressing", 0.10),
                ("mid", "progressing", 0.50),
                ("high", "progressing", 0.80),
                ("dormant_one", "dormant", 0.05),
            ]
        )

        foreground, background = select_foreground_tropes(snap.active_tropes)

        # Foreground: K highest-progress progressing tropes.
        assert [t.id for t in foreground] == ["high", "mid"][:FOREGROUND_K], (
            f"Foreground rank wrong; got {[t.id for t in foreground]}, "
            f"expected top-{FOREGROUND_K} by progress"
        )
        # Background: remaining progressing tropes only — dormants are
        # NOT background, they are queued (different concept).
        background_ids = {t.id for t in background}
        assert "dormant_one" not in background_ids, (
            "dormant tropes must not appear in background; they are "
            "queued, not a Valley-zone summary item."
        )

    def test_split_returns_empty_for_no_progressing(self) -> None:
        """Zero-byte-leak per orchestrator's prompt-section discipline:
        when no tropes are progressing both fields are empty so the
        narrator prompt registers neither section.
        """

        from sidequest.game.trope_tick import select_foreground_tropes

        snap = _seed_snapshot(
            [
                ("dormant_one", "dormant", 0.05),
                ("resolved_one", "resolved", 1.00),
            ]
        )

        foreground, background = select_foreground_tropes(snap.active_tropes)

        assert list(foreground) == [], (
            f"No progressing tropes → foreground must be empty; got {[t.id for t in foreground]}"
        )
        assert list(background) == [], (
            f"No progressing tropes → background must be empty; got {[t.id for t in background]}"
        )

    def test_split_handles_equal_progress_deterministically(self) -> None:
        """With equal progress, the split must be stable (sort by id as
        secondary key). Otherwise the narrator prompt churns turn-to-
        turn on tied tropes — exactly the prose oscillation the story
        is closing.
        """

        from sidequest.game.trope_tick import select_foreground_tropes

        snap = _seed_snapshot(
            [
                ("zebra", "progressing", 0.40),
                ("alpha", "progressing", 0.40),
                ("mango", "progressing", 0.40),
            ]
        )

        foreground_a, _ = select_foreground_tropes(snap.active_tropes)
        foreground_b, _ = select_foreground_tropes(snap.active_tropes)

        assert [t.id for t in foreground_a] == [t.id for t in foreground_b], (
            "Tied-progress tropes must produce a stable foreground "
            "ordering; observed nondeterminism breaks prompt continuity."
        )
