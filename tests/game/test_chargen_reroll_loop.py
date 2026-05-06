"""Class qualification reroll loop test.

Forces all stats below 9 on first roll, ≥9 on second; verifies the
builder rerolls until at least one class qualifies."""

import random

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry import trace

from sidequest.game.builder import CharacterBuilder
from sidequest.genre.models.character import (
    CharCreationScene,
    ClassDef,
    MechanicalEffects,
)
from sidequest.genre.models.rules import RulesConfig


class _ScriptedRandom(random.Random):
    """Returns predetermined ints in sequence; falls back to seeded random."""

    def __init__(self, scripted: list[int]):
        super().__init__()
        self._scripted = list(scripted)
        self._fallback = random.Random(42)

    def randint(self, a: int, b: int) -> int:  # type: ignore[override]
        if self._scripted:
            return self._scripted.pop(0)
        return self._fallback.randint(a, b)

    def randrange(self, *args, **kwargs):  # type: ignore[override]
        if self._scripted:
            return self._scripted.pop(0)
        return self._fallback.randrange(*args, **kwargs)


def _make_classes() -> list[ClassDef]:
    return [
        ClassDef(
            id="fighter",
            display_name="Fighter",
            rpg_role="tank",
            jungian_default="hero",
            prime_requisite="STR",
            minimum_score=9,
            kit_table="fighter_kit",
        ),
    ]


def _make_minimal_rules() -> RulesConfig:
    return RulesConfig(
        stat_generation="roll_3d6_strict",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )


def _make_roll_scene(*, qualification_loop: bool = True) -> CharCreationScene:
    return CharCreationScene(
        id="the_roll",
        title="Roll",
        narration="...",
        mechanical_effects=MechanicalEffects(
            stat_generation="roll_3d6_strict",
            class_qualification_loop=qualification_loop,
        ),
    )


def test_reroll_fires_when_no_class_qualifies():
    """First 18 dice = ones (3 per stat → all stats = 3 → no qualifying class).
    Second 18 dice = sixes (all stats = 18 → fighter qualifies).
    Builder must reroll once via apply_freeform with classes attached."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # The apply_freeform path unconditionally re-rolls with the qualification
    # loop. Construction eager-roll fires with 18 ones (all stats = 3);
    # classes not yet attached so no loop at that point.
    # apply_freeform fires again — this time classes are attached, so the
    # loop rejects the first roll (all 1s) and accepts the second (all 6s).
    scene = CharCreationScene(
        id="the_roll",
        title="Roll",
        narration="...",
        allows_freeform=True,
        mechanical_effects=MechanicalEffects(
            stat_generation="roll_3d6_strict",
            class_qualification_loop=True,
        ),
    )
    # Construction uses 18 ones (eager roll). apply_freeform uses next 36
    # dice: 18 ones (rejected) then 18 sixes (accepted).
    rng = _ScriptedRandom([1] * 18 + [1] * 18 + [6] * 18)
    rules = _make_minimal_rules()
    builder = CharacterBuilder([scene], rules, rng=rng)
    builder.with_classes(_make_classes())

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("test_span"):
        builder.apply_freeform("some text")

    # Final stats must all be 18 (from the second roll in the loop).
    rolled = builder.rolled_stats()
    assert rolled is not None
    for _name, value in rolled:
        assert value == 18

    # At least one reroll event should have been emitted.
    events = [e for span in exporter.get_finished_spans() for e in span.events]
    reroll_events = [e for e in events if e.name == "chargen.class_qualification_reroll"]
    assert len(reroll_events) >= 1


def test_no_reroll_when_classes_not_attached():
    """When with_classes() is not called (empty _classes), loop never fires even
    if class_qualification_loop=True. The apply_freeform roll sticks regardless."""
    scene = CharCreationScene(
        id="the_roll",
        title="Roll",
        narration="...",
        allows_freeform=True,
        mechanical_effects=MechanicalEffects(
            stat_generation="roll_3d6_strict",
            class_qualification_loop=True,
        ),
    )
    # Construction eager-roll uses 18 ones; apply_freeform uses the next 18 ones.
    # No loop fires — no classes attached.
    rng = _ScriptedRandom([1] * 36)
    rules = _make_minimal_rules()
    builder = CharacterBuilder([scene], rules, rng=rng)
    # Intentionally do NOT call with_classes()

    builder.apply_freeform("some text")

    rolled = builder.rolled_stats()
    assert rolled is not None
    for _name, value in rolled:
        assert value == 3  # All ones = 3 per stat (apply_freeform roll)


def test_reroll_safety_cap():
    """All-1s forever should raise RuntimeError after 100 rerolls."""
    scenes = [
        CharCreationScene(
            id="the_roll",
            title="Roll",
            narration="...",
            mechanical_effects=MechanicalEffects(
                stat_generation="roll_3d6_strict",
                class_qualification_loop=True,
            ),
        ),
    ]
    rules = _make_minimal_rules()
    # 18 dice per roll * 102 rolls = 1836 ones (more than enough for the cap).
    rng = _ScriptedRandom([1] * 1836)
    builder = CharacterBuilder(scenes, rules, rng=rng)
    builder.with_classes(_make_classes())

    with pytest.raises(RuntimeError, match="exceeded 100 rerolls"):
        builder._roll_3d6_with_qualification(qualification_loop=True)
