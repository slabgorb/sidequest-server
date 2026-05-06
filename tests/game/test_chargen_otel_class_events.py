"""OTEL events for chargen class subsystem.

Verifies that chargen.class_qualifying, chargen.class_chosen, and
chargen.class_kit_rolled events are emitted at the correct decision points.
"""

from __future__ import annotations

import random

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.game.builder import CharacterBuilder
from sidequest.genre.models.character import (
    CharCreationChoice,
    CharCreationScene,
    ClassDef,
    EquipmentTables,
    MechanicalEffects,
)
from sidequest.genre.models.rules import RulesConfig


def _fresh_otel() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Create a fresh, isolated OTEL provider + exporter pair.

    Each test gets its own provider so there's no cross-test pollution.
    The provider is NOT set as the global — tests use provider.get_tracer()
    directly, which works with trace.get_current_span() because the span
    context is thread-local (not global-provider-dependent).
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _events_by_name(exporter: InMemorySpanExporter) -> dict[str, list]:
    """Collect all finished-span events grouped by name."""
    result: dict[str, list] = {}
    for span in exporter.get_finished_spans():
        for event in span.events:
            result.setdefault(event.name, []).append(event)
    return result


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
        ClassDef(
            id="mage",
            display_name="Mage",
            rpg_role="dps",
            jungian_default="sage",
            prime_requisite="INT",
            minimum_score=9,
            kit_table="mage_kit",
        ),
    ]


def _make_equipment_tables() -> EquipmentTables:
    return EquipmentTables(
        tables={},
        rolls_per_slot={},
        class_tables={
            "fighter_kit": {
                "weapon": ["sword_long"],
                "armor": ["plate_mail"],
            },
        },
    )


def _make_full_chargen_scenes() -> list[CharCreationScene]:
    """Three-scene flow: roll stats → class choice → class kit equipment."""
    return [
        CharCreationScene(
            id="the_roll",
            title="Roll",
            narration="Roll your stats.",
            mechanical_effects=MechanicalEffects(
                stat_generation="roll_3d6_strict",
                class_qualification_loop=True,
            ),
        ),
        CharCreationScene(
            id="the_calling",
            title="Choose Class",
            narration="What are you?",
            choices=[
                CharCreationChoice(
                    label="Fighter",
                    description="A warrior.",
                    mechanical_effects=MechanicalEffects(class_hint="Fighter"),
                ),
                CharCreationChoice(
                    label="Mage",
                    description="A wizard.",
                    mechanical_effects=MechanicalEffects(class_hint="Mage"),
                ),
            ],
        ),
        CharCreationScene(
            id="the_kit",
            title="Equipment",
            narration="Your gear.",
            mechanical_effects=MechanicalEffects(equipment_generation="class_kit"),
        ),
    ]


def test_class_otel_events_emitted():
    """Full chargen walk emits class_qualifying, class_chosen, class_kit_rolled."""
    provider, exporter = _fresh_otel()
    tracer = provider.get_tracer("test_full")

    rules = RulesConfig(
        stat_generation="roll_3d6_strict",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )
    classes = _make_classes()
    tables = _make_equipment_tables()

    # Force-stub stats to all-18 by calling _roll_3d6_with_qualification
    # path is harder to inject; instead override _rolled_stats directly
    # after construction so both Fighter and Mage qualify deterministically.
    # Equipment rolls then use a fresh seeded RNG.
    rng = random.Random(42)

    builder = (
        CharacterBuilder(_make_full_chargen_scenes(), rules, rng=rng)
        .with_classes(classes)
        .with_equipment_tables(tables)
    )
    # Force qualifying stats so Fighter is at idx 0 of the filtered scene.
    builder._rolled_stats = [
        ("STR", 18), ("DEX", 18), ("CON", 18),
        ("INT", 18), ("WIS", 18), ("CHA", 18),
    ]

    with tracer.start_as_current_span("chargen_span"):
        # Scene 0: auto-advance (the_roll) — construction already rolled,
        # auto_advance fires _roll_3d6_with_qualification if not already rolled.
        builder.apply_auto_advance()
        # Scene 1: choose Fighter (index 0) — emits class_chosen
        builder.apply_choice(0)
        # Scene 2: auto-advance (the_kit) — emits class_kit_rolled
        builder.apply_auto_advance()

    assert builder.is_confirmation()

    with tracer.start_as_current_span("build_span"):
        character = builder.build("Gareth")
    assert character.char_class == "Fighter"

    events = _events_by_name(exporter)

    # chargen.class_qualifying must have fired (stats were rolled at construction).
    assert "chargen.class_qualifying" in events, (
        f"Missing chargen.class_qualifying. Got events: {list(events.keys())}"
    )

    # chargen.class_chosen must have fired with correct hint.
    assert "chargen.class_chosen" in events, (
        f"Missing chargen.class_chosen. Got events: {list(events.keys())}"
    )
    class_chosen_events = events["chargen.class_chosen"]
    assert any(
        e.attributes.get("class_hint") == "Fighter" for e in class_chosen_events
    )

    # chargen.class_kit_rolled must have fired.
    assert "chargen.class_kit_rolled" in events, (
        f"Missing chargen.class_kit_rolled. Got events: {list(events.keys())}"
    )
    kit_rolled = events["chargen.class_kit_rolled"][0]
    assert str(kit_rolled.attributes["kit_id"]).startswith("class_kit:")


def test_class_qualifying_emits_qualifying_list():
    """class_qualifying event must carry class_ids attribute."""
    provider, exporter = _fresh_otel()
    tracer = provider.get_tracer("test_qualifying")

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
    rules = RulesConfig(
        stat_generation="roll_3d6_strict",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )
    # All sixes → STR=18, INT=18 → both Fighter and Mage qualify.
    # Construction uses 18 dice, apply_freeform uses 18 more.
    rng = _ScriptedRandom([6] * 36)

    builder = (
        CharacterBuilder([scene], rules, rng=rng)
        .with_classes(_make_classes())
    )

    with tracer.start_as_current_span("roll_span"):
        builder.apply_freeform("text")

    events = _events_by_name(exporter)
    assert "chargen.class_qualifying" in events, (
        f"Missing chargen.class_qualifying. Got: {list(events.keys())}"
    )
    qualifying_event = events["chargen.class_qualifying"][0]
    class_ids = qualifying_event.attributes["class_ids"]
    assert "fighter" in class_ids
    assert "mage" in class_ids


def test_class_chosen_only_on_player_choice():
    """class_chosen fires on apply_choice but NOT on apply_auto_advance."""
    provider, exporter = _fresh_otel()
    tracer = provider.get_tracer("test_chosen")

    # A single auto-advance scene with class_hint in scene mechanical_effects
    # (NOT a player choice). class_chosen should NOT fire.
    auto_scene = CharCreationScene(
        id="auto",
        title="Auto",
        narration="...",
        mechanical_effects=MechanicalEffects(class_hint="Fighter"),
    )
    rules = RulesConfig(
        stat_generation="standard_array",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )
    builder = CharacterBuilder([auto_scene], rules, rng=random.Random(42))

    with tracer.start_as_current_span("auto_span"):
        builder.apply_auto_advance()

    events = _events_by_name(exporter)
    # Should NOT have class_chosen — auto_advance sets class_hint
    # mechanically, not through a player choice.
    assert "chargen.class_chosen" not in events, (
        "class_chosen should not fire on auto_advance (only on apply_choice)"
    )


def test_class_chosen_fires_on_apply_choice():
    """class_chosen fires precisely when a player selects a class-hint choice."""
    provider, exporter = _fresh_otel()
    tracer = provider.get_tracer("test_chosen_fires")

    scene = CharCreationScene(
        id="class_choice",
        title="Choose",
        narration="...",
        choices=[
            CharCreationChoice(
                label="Fighter",
                description="A warrior.",
                mechanical_effects=MechanicalEffects(class_hint="Fighter"),
            ),
        ],
    )
    rules = RulesConfig(
        stat_generation="standard_array",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )
    builder = CharacterBuilder([scene], rules, rng=random.Random(42))

    with tracer.start_as_current_span("choice_span"):
        builder.apply_choice(0)

    events = _events_by_name(exporter)
    assert "chargen.class_chosen" in events
    evt = events["chargen.class_chosen"][0]
    assert evt.attributes["class_hint"] == "Fighter"


class _ScriptedRandom(random.Random):
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
