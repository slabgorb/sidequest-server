"""Tension tracker — dual-track pacing model for combat drama.

Port of ``sidequest-api/crates/sidequest-game/src/tension_tracker.rs`` (803 LOC).

Tracks two independent tension axes:

- ``action_tension`` (gambler's ramp): rises during consecutive low-action
  turns, drops when something dramatic happens. Measures how "overdue"
  action is.
- ``stakes_tension`` (HP-based): rises as characters take damage or are in
  danger, drops as they heal/rest. Measures how much is at stake.

The combined ``drama_weight`` is ``max(action_tension, stakes_tension,
effective_spike)`` with per-event linear decay on the spike.

The Python API mirrors the Rust source 1:1:

- ``TensionTracker()`` / ``TensionTracker.with_values(action, stakes)``
- ``action_tension()`` / ``stakes_tension()`` / ``drama_weight()`` /
  ``active_spike()`` / ``boring_streak()``
- ``inject_spike(amount)``
- ``record_event(CombatEvent)``
- ``update_stakes(current_hp, max_hp)``
- ``tick()``  — no args; decays action_tension and ages spike
- ``observe(round, killed, lowest_hp_ratio) -> TurnClassification``
- ``pacing_hint(thresholds: DramaThresholds) -> PacingHint``

Free functions:

- ``classify_round(round, killed) -> CombatEvent``
- ``classify_combat_outcome(round, killed, lowest_hp_ratio) -> TurnClassification``

``DramaThresholds`` is loaded from the genre pack via ``sidequest.genre``
and passed in by the caller — never read from Python-side constants
(matches Rust ``DramaThresholds`` in ``sidequest-genre``).

Stories: 5-1 (dual-track), 5-2 (event classification), 5-7 (pacing hint
narrator wiring), Epic 42 / 42-3 (Python port).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants — mirror Rust file-private const items
# ---------------------------------------------------------------------------

#: Base increment per boring turn, multiplied by streak count.
_BORING_BASE: float = 0.05
#: Multiplicative decay factor for action tension per tick.
_ACTION_DECAY: float = 0.9
#: Default per-turn decay rate for spikes injected via ``inject_spike()``.
_DEFAULT_SPIKE_DECAY_RATE: float = 0.15
#: Total round damage at or above this is dramatic.
_DRAMATIC_DAMAGE_THRESHOLD: int = 15
#: HP ratio threshold below which a surviving target triggers NearMiss.
_NEAR_MISS_HP_THRESHOLD: float = 0.2


def _clamp01(v: float) -> float:
    """Clamp a float to ``[0.0, 1.0]``. Mirror of Rust ``clamp01``."""
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


# ---------------------------------------------------------------------------
# Damage / round payloads
# ---------------------------------------------------------------------------


class DamageEvent(BaseModel):
    """A damage event within a combat round — used for tension classification.

    Port of Rust ``DamageEvent``. Fields keep the same names and types
    (Rust ``i32`` damage maps to Python ``int``).
    """

    model_config = {"extra": "forbid"}

    attacker: str
    target: str
    damage: int
    round: int


class RoundResult(BaseModel):
    """Result of resolving one combat round — used for tension classification.

    Port of Rust ``RoundResult``.
    """

    model_config = {"extra": "forbid"}

    round: int
    damage_events: list[DamageEvent] = Field(default_factory=list)
    effects_applied: list[str] = Field(default_factory=list)
    effects_expired: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DeliveryMode(StrEnum):
    """Drama-aware text delivery mode — controls how narration is revealed.

    Port of Rust ``DeliveryMode`` (``#[non_exhaustive]`` in Rust; Python
    enums are open for extension by adding a member here).
    """

    Instant = "Instant"
    Sentence = "Sentence"
    Streaming = "Streaming"


class CombatEvent(StrEnum):
    """Combat event classification for the gambler's ramp.

    Port of Rust ``CombatEvent``.
    """

    Boring = "Boring"
    Dramatic = "Dramatic"
    Normal = "Normal"


class DetailedCombatEvent(StrEnum):
    """Specific dramatic combat events with spike magnitudes.

    Port of Rust ``DetailedCombatEvent`` (``#[non_exhaustive]``). Add a
    member here to extend; the ``spike_magnitude`` and ``decay_rate``
    methods must grow a matching arm.
    """

    CriticalHit = "CriticalHit"
    KillingBlow = "KillingBlow"
    DeathSave = "DeathSave"
    FirstBlood = "FirstBlood"
    NearMiss = "NearMiss"
    LastStanding = "LastStanding"

    def spike_magnitude(self) -> float:
        """Tension spike magnitude for this event type (0.0–1.0)."""
        return _SPIKE_MAGNITUDE[self]

    def decay_rate(self) -> float:
        """Per-turn decay rate for the spike injected by this event."""
        return _DECAY_RATE[self]


_SPIKE_MAGNITUDE: dict[DetailedCombatEvent, float] = {
    DetailedCombatEvent.CriticalHit: 0.8,
    DetailedCombatEvent.KillingBlow: 1.0,
    DetailedCombatEvent.DeathSave: 0.7,
    DetailedCombatEvent.FirstBlood: 0.6,
    DetailedCombatEvent.NearMiss: 0.5,
    DetailedCombatEvent.LastStanding: 0.9,
}

_DECAY_RATE: dict[DetailedCombatEvent, float] = {
    DetailedCombatEvent.CriticalHit: 0.15,
    DetailedCombatEvent.KillingBlow: 0.20,
    DetailedCombatEvent.DeathSave: 0.15,
    DetailedCombatEvent.FirstBlood: 0.10,
    DetailedCombatEvent.NearMiss: 0.10,
    DetailedCombatEvent.LastStanding: 0.20,
}


# ---------------------------------------------------------------------------
# TurnClassification — algebraic enum (Boring | Normal | Dramatic(event))
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnClassification:
    """Classification of a combat turn for pacing decisions.

    Port of Rust enum::

        enum TurnClassification {
            Boring,
            Dramatic(DetailedCombatEvent),
            Normal,
        }

    Modeled as a frozen dataclass with a discriminator (``kind``) and an
    optional payload (``event``, only set when ``kind == "Dramatic"``).
    Use the ``boring()``, ``normal()``, ``dramatic(event)`` factories.
    """

    kind: str
    event: DetailedCombatEvent | None = None

    @classmethod
    def boring(cls) -> TurnClassification:
        return cls(kind="Boring", event=None)

    @classmethod
    def normal(cls) -> TurnClassification:
        return cls(kind="Normal", event=None)

    @classmethod
    def dramatic(cls, event: DetailedCombatEvent) -> TurnClassification:
        return cls(kind="Dramatic", event=event)


# ---------------------------------------------------------------------------
# PacingHint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PacingHint:
    """Pacing guidance for a single turn — computed from TensionTracker state.

    Port of Rust ``PacingHint``.

    - ``drama_weight``: combined drama metric (0.0–1.0)
    - ``target_sentences``: suggested narration length (1–6)
    - ``delivery_mode``: how the client should reveal the narration text
    - ``escalation_beat``: optional directive when boring streak crosses
      the genre's ``escalation_streak`` threshold
    """

    drama_weight: float
    target_sentences: int
    delivery_mode: DeliveryMode
    escalation_beat: str | None = None

    def narrator_directive(self) -> str:
        """Produce a narrator-facing directive string for prompt injection.

        Format is byte-identical to Rust:

            "Target approximately N sentence(s) for this narration.
             Drama level: P%."

        where P is ``drama_weight * 100`` formatted with zero decimals.
        """
        return (
            f"Target approximately {self.target_sentences} sentence(s) for this narration. "
            f"Drama level: {self.drama_weight * 100.0:.0f}%."
        )


# ---------------------------------------------------------------------------
# Internal: event spike with linear decay
# ---------------------------------------------------------------------------


@dataclass
class _EventSpike:
    """A single event-driven tension spike with per-event decay.

    Port of private Rust ``EventSpike`` struct.
    """

    magnitude: float
    decay_rate: float


# ---------------------------------------------------------------------------
# TensionTracker
# ---------------------------------------------------------------------------


class TensionTracker:
    """Dual-track tension model combining action tension (gambler's ramp)
    and stakes tension (HP-based).

    Port of Rust ``TensionTracker``. State fields are kept private and
    exposed via accessor methods (mirrors Rust's accessor pattern; Rust
    fields are private by default and exposed via ``pub fn``).
    """

    def __init__(self) -> None:
        self._action_tension: float = 0.0
        self._stakes_tension: float = 0.0
        self._last_event_spike: _EventSpike | None = None
        self._spike_decay_age: int = 0
        self._boring_streak: int = 0

    @classmethod
    def with_values(cls, action: float, stakes: float) -> TensionTracker:
        """Create a tracker with custom initial values, clamped to 0.0–1.0."""
        tracker = cls()
        tracker._action_tension = _clamp01(action)
        tracker._stakes_tension = _clamp01(stakes)
        return tracker

    # --- Accessors ----------------------------------------------------

    def action_tension(self) -> float:
        """Current action tension (gambler's ramp track)."""
        return self._action_tension

    def stakes_tension(self) -> float:
        """Current stakes tension (HP-based track)."""
        return self._stakes_tension

    def drama_weight(self) -> float:
        """Combined drama metric: ``max(action, stakes, effective_spike)``,
        clamped to 1.0.
        """
        return _clamp01(
            max(self._action_tension, self._stakes_tension, self._effective_spike())
        )

    def active_spike(self) -> float:
        """Current effective spike value after linear decay."""
        return self._effective_spike()

    def boring_streak(self) -> int:
        """Consecutive boring turns without a dramatic event."""
        return self._boring_streak

    # --- Mutators -----------------------------------------------------

    def inject_spike(self, amount: float) -> None:
        """Inject a temporary drama spike, replacing any existing spike."""
        self._last_event_spike = _EventSpike(
            magnitude=_clamp01(amount),
            decay_rate=_DEFAULT_SPIKE_DECAY_RATE,
        )
        self._spike_decay_age = 0

    def record_event(self, event: CombatEvent) -> None:
        """Record a combat event, updating action tension via the
        gambler's ramp.
        """
        if event == CombatEvent.Boring:
            self._boring_streak += 1
            self._action_tension = _clamp01(
                self._action_tension + _BORING_BASE * float(self._boring_streak)
            )
        elif event == CombatEvent.Dramatic:
            self._action_tension = 0.0
            self._boring_streak = 0
        elif event == CombatEvent.Normal:
            # No effect on action tension — matches Rust's empty arm.
            pass

    def update_stakes(self, current_hp: int, max_hp: int) -> None:
        """Update stakes tension from HP values.

        ``stakes = 1.0 - (current / max)``. Mirrors Rust ``debug_assert!``
        on positive max_hp via Python ``assert``.
        """
        assert max_hp > 0, "max_hp must be positive"
        self._stakes_tension = _clamp01(1.0 - float(current_hp) / float(max_hp))

    def tick(self) -> None:
        """Advance one tick: decay action tension and age spike. Stakes
        are HP-driven only.
        """
        self._action_tension *= _ACTION_DECAY
        self._age_spike()

    # --- Pacing hint --------------------------------------------------

    def pacing_hint(self, thresholds: DramaThresholds) -> PacingHint:
        """Compute a pacing hint from the current tension state and
        genre thresholds.

        Thresholds are passed in (sourced from genre pack) — never
        read from Python-side constants.
        """
        dw = self.drama_weight()

        if dw > thresholds.streaming_delivery_min:
            delivery_mode = DeliveryMode.Streaming
        elif dw >= thresholds.sentence_delivery_min:
            delivery_mode = DeliveryMode.Sentence
        else:
            delivery_mode = DeliveryMode.Instant

        # Linear interpolation: 1 + floor(drama_weight * 5), range 1–6.
        target_sentences = 1 + int(math.floor(dw * 5.0))

        if self._boring_streak >= thresholds.escalation_streak:
            escalation_beat: str | None = (
                "The environment shifts — introduce a new element to break the monotony."
            )
        else:
            escalation_beat = None

        return PacingHint(
            drama_weight=dw,
            target_sentences=target_sentences,
            delivery_mode=delivery_mode,
            escalation_beat=escalation_beat,
        )

    # --- Combat-round observation ------------------------------------

    def observe(
        self,
        round: RoundResult,
        killed: str | None,
        lowest_hp_ratio: float | None,
    ) -> TurnClassification:
        """Observe a combat round: age existing spike, classify the
        outcome, update boring_streak, inject spike for dramatic events
        with per-event decay.
        """
        # 1. Age any existing spike before processing new events.
        self._age_spike()

        classification = classify_combat_outcome(round, killed, lowest_hp_ratio)

        if classification.kind == "Boring":
            self.record_event(CombatEvent.Boring)
        elif classification.kind == "Dramatic":
            self.record_event(CombatEvent.Dramatic)
            # Replace spike with per-event magnitude and decay rate.
            assert classification.event is not None
            self._last_event_spike = _EventSpike(
                magnitude=classification.event.spike_magnitude(),
                decay_rate=classification.event.decay_rate(),
            )
            self._spike_decay_age = 0
        else:  # Normal
            self.record_event(CombatEvent.Normal)

        return classification

    # --- Private helpers ---------------------------------------------

    def _effective_spike(self) -> float:
        """Effective spike value after linear decay. Returns 0.0 if no
        spike is active.
        """
        if self._last_event_spike is None:
            return 0.0
        spike = self._last_event_spike
        return max(spike.magnitude - spike.decay_rate * float(self._spike_decay_age), 0.0)

    def _age_spike(self) -> None:
        """Age the spike by one turn. Cleans up fully decayed spikes."""
        if self._last_event_spike is None:
            return
        self._spike_decay_age += 1
        if self._effective_spike() <= 0.0:
            self._last_event_spike = None
            self._spike_decay_age = 0


# ---------------------------------------------------------------------------
# Free functions — round / outcome classification
# ---------------------------------------------------------------------------


def classify_round(round: RoundResult, killed: str | None) -> CombatEvent:
    """Classify a combat round result as Boring, Dramatic, or Normal.

    Rules (mirror Rust):

    - Dramatic: a combatant was killed (``killed`` is not None — empty
      string still counts), total damage >= dramatic threshold, or new
      status effects were applied.
    - Boring: zero effective damage and no new effects.
    - Normal: some damage dealt but below the dramatic threshold, no
      kills or effects.
    """
    # A kill is always dramatic. Note: Rust ``Option<&str>`` distinguishes
    # ``Some("")`` from ``None`` — mirror with ``killed is not None``.
    if killed is not None:
        return CombatEvent.Dramatic

    # New status effects are dramatic.
    if round.effects_applied:
        return CombatEvent.Dramatic

    # Negative damage is clamped to zero per event (matches Rust ``e.damage.max(0)``).
    total_damage: int = sum(max(e.damage, 0) for e in round.damage_events)

    if total_damage >= _DRAMATIC_DAMAGE_THRESHOLD:
        return CombatEvent.Dramatic

    if total_damage == 0:
        return CombatEvent.Boring

    return CombatEvent.Normal


def classify_combat_outcome(
    round: RoundResult,
    killed: str | None,
    lowest_hp_ratio: float | None,
) -> TurnClassification:
    """Classify a combat round into a detailed turn classification.

    Priority ordering (mirror Rust): kill → near miss (low HP) → critical
    hit (high damage) → effects → normal → boring.

    - ``round``: the combat round result with damage events and effects.
    - ``killed``: name of a combatant who died this round, if any (empty
      string still counts as a kill).
    - ``lowest_hp_ratio``: the lowest HP ratio (current/max) of any
      targeted combatant after damage, or ``None`` if unknown. Used to
      detect NearMiss events.
    """
    # Kill is always KillingBlow — highest priority.
    if killed is not None:
        return TurnClassification.dramatic(DetailedCombatEvent.KillingBlow)

    total_damage: int = sum(max(e.damage, 0) for e in round.damage_events)

    # Near miss — target survived at low HP.
    if lowest_hp_ratio is not None:
        if lowest_hp_ratio <= _NEAR_MISS_HP_THRESHOLD and total_damage > 0:
            return TurnClassification.dramatic(DetailedCombatEvent.NearMiss)

    # Critical hit — high total damage.
    if total_damage >= _DRAMATIC_DAMAGE_THRESHOLD:
        return TurnClassification.dramatic(DetailedCombatEvent.CriticalHit)

    # Status effects are dramatic (FirstBlood-level).
    if round.effects_applied:
        return TurnClassification.dramatic(DetailedCombatEvent.FirstBlood)

    # No damage at all — boring.
    if total_damage == 0:
        return TurnClassification.boring()

    # Some damage but not dramatic.
    return TurnClassification.normal()


# ---------------------------------------------------------------------------
# Forward-declared import to avoid a circular dependency at module load
# (``DramaThresholds`` lives in ``sidequest.genre.models.ocean`` which
# transitively imports nothing from the game module — safe to import at
# top-level, but kept here for clarity of the type used in
# :meth:`TensionTracker.pacing_hint`).
# ---------------------------------------------------------------------------


from sidequest.genre.models.ocean import DramaThresholds  # noqa: E402  (cycle-safety placement)


__all__ = [
    "CombatEvent",
    "DamageEvent",
    "DeliveryMode",
    "DetailedCombatEvent",
    "PacingHint",
    "RoundResult",
    "TensionTracker",
    "TurnClassification",
    "classify_combat_outcome",
    "classify_round",
]
