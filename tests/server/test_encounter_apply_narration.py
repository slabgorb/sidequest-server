from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.agents.orchestrator import BeatSelection, NarrationTurnResult, NpcMention
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import load_genre_pack

# Load directly from the fixture pack on disk, bypassing GenreLoader's
# session-wide cache. The session cache keys on slug only, so another test
# that loads ``caverns_and_claudes`` from ``sidequest-content/`` (e.g.
# test_opening_turn_bootstrap.py) poisons the cache with the real content
# pack — which is missing the ``mutant_ability`` / ``flank`` beats these
# tests need. load_genre_pack() is the cache-free path.
_FIXTURE_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "test_genre"
)


def _load_pack(_genre: str):
    # ``_genre`` is intentionally ignored — every fixture slug symlinks to
    # the same ``test_genre`` directory, and we skip the cache to avoid
    # cross-test pack poisoning. Callers still pass a slug for readability.
    return load_genre_pack(_FIXTURE_PACK)


@pytest.fixture
def cac_snap():
    snap = GameSnapshot(genre="caverns_and_claudes")
    pack = _load_pack("caverns_and_claudes")
    return snap, pack


@pytest.fixture
def otel_capture():
    """Attach an in-memory exporter to the running TracerProvider.

    Mirrors the otel_capture fixture in test_room_graph_init.py — adds a
    SimpleSpanProcessor alongside the existing processors so span emissions
    from production code fan out to the in-memory sink for assertion.
    """
    from sidequest.telemetry.setup import init_tracer

    init_tracer()  # idempotent
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), (
        f"expected SDK TracerProvider, got {type(provider)!r}"
    )
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


def test_narrator_confrontation_trigger_creates_encounter(cac_snap) -> None:
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot
    snap, pack = cac_snap
    result = NarrationTurnResult(
        narration="Goblins leap from the shadows.",
        confrontation="combat",
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    assert snap.encounter is not None
    assert snap.encounter.encounter_type == "combat"


def test_beat_selection_applied_bumps_metric(cac_snap) -> None:
    from sidequest.game.encounter import StructuredEncounter
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot
    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    from sidequest.game.encounter import EncounterMetric, MetricDirection
    enc.metric = EncounterMetric(
        name="momentum", current=0, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="The blade sings.",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    assert snap.encounter.beat == 1
    assert snap.encounter.metric.current == 2


def test_beat_selection_unknown_beat_id_raises(cac_snap) -> None:
    from sidequest.game.encounter import StructuredEncounter
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot
    snap, pack = cac_snap
    snap.encounter = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="tap_dance", target=None)],
    )
    with pytest.raises(ValueError, match="unknown beat_id"):
        _apply_narration_result_to_snapshot(
            snap, result, player_name="Rux", pack=pack,
        )


def test_metric_crossing_threshold_resolves_encounter(cac_snap) -> None:
    from sidequest.game.encounter import EncounterMetric, MetricDirection, StructuredEncounter
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot
    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum", current=9, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    assert snap.encounter.resolved is True
    assert snap.encounter.structured_phase.value == "Resolution"


def test_ascending_metric_ignores_threshold_low(cac_snap) -> None:
    """Ascending encounters resolve only on threshold_high, never threshold_low.

    Port of Rust encounter.rs direction-aware threshold check. Prevents a chase
    (ascending) from being falsely resolved if the counter dips below zero.
    """
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    # Ascending metric sitting below threshold_low — must NOT resolve.
    enc.metric = EncounterMetric(
        name="distance",
        current=-5,
        starting=0,
        direction=MetricDirection.Ascending,
        threshold_high=10,
        threshold_low=0,
    )
    enc.encounter_type = "combat"  # still matched against cac pack
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    # attack metric_delta=2 → -5+2=-3, then clamped to 0 by Ascending rule.
    # Neither threshold_high (10) nor threshold_low (0) triggers resolution
    # on an Ascending metric when only threshold_low-equivalent is crossed.
    assert snap.encounter.resolved is False
    assert snap.encounter.metric.current == 0  # clamped from -3


def test_descending_metric_ignores_threshold_high(cac_snap) -> None:
    """Descending encounters resolve only on threshold_low, never threshold_high."""
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="courage",
        current=12,
        starting=10,
        direction=MetricDirection.Descending,
        threshold_high=10,
        threshold_low=0,
    )
    enc.encounter_type = "combat"
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    # Descending metric now at 14; threshold_high=10 is crossed but must be
    # ignored. Only threshold_low=0 resolves on Descending.
    assert snap.encounter.resolved is False
    assert snap.encounter.metric.current == 14


def test_phase_ladder_beat_four_is_escalation(cac_snap) -> None:
    """Beat 4 is still Escalation; Climax starts at beat 5 (matches Rust)."""
    from sidequest.game.encounter import (
        EncounterMetric,
        EncounterPhase,
        MetricDirection,
        StructuredEncounter,
    )
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum",
        current=0,
        starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=100,  # wide so we don't resolve on threshold
        threshold_low=-100,
    )
    enc.beat = 3
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    assert snap.encounter.beat == 4
    assert snap.encounter.structured_phase == EncounterPhase.Escalation


# ---------------------------------------------------------------------------
# Lie-detector: confrontation-trigger with empty npcs_present
# (pingpong 2026-04-24 — "Confrontation panel has no enemy combatants")
# ---------------------------------------------------------------------------


def test_confrontation_trigger_with_empty_npcs_present_fires_empty_actor_list_span(
    cac_snap, otel_capture: InMemorySpanExporter
) -> None:
    """Narrator emits confrontation but no npcs_present → encounter is
    instantiated with only the player, and the lie-detector span fires so the
    GM panel can surface that the extraction dropped the adversary list.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    snap.genre_slug = "caverns_and_claudes"
    result = NarrationTurnResult(
        narration="Goblins leap from the shadows.",
        confrontation="combat",
        npcs_present=[],  # narrator named goblins in prose but omitted them here
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )

    # Encounter still instantiated (with player-only combatant list)
    assert snap.encounter is not None
    assert snap.encounter.encounter_type == "combat"

    # Lie-detector span fired
    spans_by_name = {s.name: s for s in otel_capture.get_finished_spans()}
    assert "encounter.empty_actor_list" in spans_by_name, (
        f"expected encounter.empty_actor_list span; got {list(spans_by_name)}"
    )
    s = spans_by_name["encounter.empty_actor_list"]
    assert s.attributes["encounter_type"] == "combat"
    assert s.attributes["player_name"] == "Rux"
    assert s.attributes["genre_slug"] == "caverns_and_claudes"


def test_confrontation_trigger_with_populated_npcs_present_does_not_fire_span(
    cac_snap, otel_capture: InMemorySpanExporter
) -> None:
    """Healthy case — when npcs_present carries adversaries, the
    lie-detector stays quiet. Asserts the span is scoped to the extraction
    failure, not every confrontation.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    snap.genre_slug = "caverns_and_claudes"
    result = NarrationTurnResult(
        narration="Goblins leap from the shadows.",
        confrontation="combat",
        npcs_present=[NpcMention(name="Goblin pack", role="hostile", is_new=True)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert "encounter.empty_actor_list" not in span_names


# ---------------------------------------------------------------------------
# ADR-074 dice integration: failed roll applies beat's failure_metric_delta
# (pingpong 2026-04-24 — "Momentum increments on a failed Use Mutation roll")
# ---------------------------------------------------------------------------


@pytest.fixture
def mw_snap_with_combat():
    """Fresh snapshot with a live Wasteland Brawl.

    Loads via the ``caverns_and_claudes`` slug because the test fixture
    directory symlinks every genre to the frozen ``test_genre`` pack (a
    mutant_wasteland copy). The resolved pack has ``type: combat`` with
    ``mutant_ability`` + ``flank`` beats carrying the new structured
    failure branch — no separate ``mutant_wasteland`` symlink exists in
    ``tests/fixtures/packs/``.
    """
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )

    snap = GameSnapshot(genre="caverns_and_claudes")
    pack = _load_pack("caverns_and_claudes")
    enc = StructuredEncounter.combat(combatants=["Slabgorb"], hp=10)
    enc.encounter_type = "combat"
    enc.metric = EncounterMetric(
        name="momentum", current=0, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    snap.encounter = enc
    return snap, pack


def test_all_beat_selections_dropped_on_dice_turn(
    mw_snap_with_combat, otel_capture: InMemorySpanExporter
) -> None:
    """SOUL Agency + "Crunch in the Genre" — on a dice-replay turn
    (``dice_failed is not None``) the player's beat was already applied
    mechanically by DICE_THROW dispatch. All narrator-extracted
    ``beat_selections`` on this turn are narrative only; mechanical
    application would (a) double-play the player's own beat (agency
    violation) or (b) silently push metric past threshold via NPC beats
    that use player-positive metric_delta values (encounter
    auto-resolves mid-fight).

    Regression for playtest 2026-04-24 pingpong entries "Player
    auto-plays 'attack' beat after failed Flank" (Agency) and
    "Confrontation tab disappears mid-fight" (resolved_encounter=False
    when the server-side phantom mechanics hit threshold_high).
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = mw_snap_with_combat
    # Narrator extracted player + NPC beats. Both must be filtered when
    # dice_failed is not None.
    result = NarrationTurnResult(
        narration="You swing, the Warden counters.",
        beat_selections=[
            BeatSelection(actor="Slabgorb", beat_id="attack", target=None),
            BeatSelection(actor="Warden", beat_id="attack", target=None),
            BeatSelection(actor="Warden", beat_id="mutant_ability", target=None),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack, dice_failed=False,
    )
    # No beat applied — momentum stays at whatever dice dispatch left it.
    assert snap.encounter.metric.current == 0

    # Same for dice_failed=True (the other branch of "dice ran").
    snap2, pack2 = mw_snap_with_combat
    snap2.encounter.metric.current = 5
    result2 = NarrationTurnResult(
        narration="The Warden channels the mutation.",
        beat_selections=[
            BeatSelection(actor="Warden", beat_id="mutant_ability", target=None),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap2, result2, player_name="Slabgorb", pack=pack2, dice_failed=True,
    )
    # Pre-dice-applied momentum is untouched. No failure-branch span fires
    # because no beat was applied at all.
    assert snap2.encounter.metric.current == 5
    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert "encounter.beat_failure_branch" not in span_names


# ---------------------------------------------------------------------------
# Narrator-granted items land on character inventory
# (pingpong 2026-04-24 — "items_gained=1 on Warden defeat — brass memory
# core never appears in Inventory")
# ---------------------------------------------------------------------------


@pytest.fixture
def cac_snap_with_character(cac_snap):
    """CAC snapshot with a minimal Character so inventory tests have a
    target for narrator-granted items. Mirrors the shape used by
    test_perception_rewriter_wiring._make_character."""
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    snap, pack = cac_snap
    core = CreatureCore(
        name="Slabgorb",
        description="A scavenger.",
        personality="Cautious.",
        inventory=Inventory(),
        statuses=[],
    )
    character = Character(
        core=core, char_class="Ranger", race="Human", backstory="A wanderer.",
    )
    snap.characters.append(character)
    return snap, pack, character


def test_items_gained_lands_on_character_inventory(
    cac_snap_with_character,
) -> None:
    """Narrator-granted items must reach ``character.core.inventory.items``.

    Regression for pingpong 2026-04-24 "items_gained=1 on Warden defeat —
    brass memory core never appears in Inventory". The orchestrator
    extracted ``items_gained`` but the server only used it for a
    watcher-patch summary — the item never landed on inventory, so the
    UI panel stayed out of sync with the narrative.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack, character = cac_snap_with_character
    assert len(character.core.inventory.items) == 0
    result = NarrationTurnResult(
        narration="In its chest cavity, a brass-cased memory core still blinks.",
        items_gained=[
            {
                "name": "Brass Memory Core",
                "description": "A scavenged data spindle; it hums when handled.",
                "category": "quest",
            },
        ],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack,
    )
    assert len(character.core.inventory.items) == 1
    item = character.core.inventory.items[0]
    assert item["name"] == "Brass Memory Core"
    assert item["category"] == "quest"
    assert item["description"].startswith("A scavenged")
    assert item["state"] == "Carried"
    assert item["equipped"] is False
    assert item["quantity"] == 1
    assert item["id"] == "narrator:brass_memory_core"


def test_items_gained_normalizes_unknown_category_to_misc(
    cac_snap_with_character,
) -> None:
    """Narrator-declared categories outside the allowed set normalize to
    ``misc`` rather than flow through unchecked — guards against the UI
    filter (``InventoryPanel``) receiving a category it can't render.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack, character = cac_snap_with_character
    result = NarrationTurnResult(
        narration="You pocket the oddity.",
        items_gained=[
            {"name": "Strange Trinket", "description": "Odd.", "category": "junk"},
        ],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack,
    )
    assert character.core.inventory.items[0]["category"] == "misc"


def test_items_lost_removes_first_matching_name_case_insensitive(
    cac_snap_with_character,
) -> None:
    """Narrator-declared ``items_lost`` removes the first matching item
    from inventory by name (case-insensitive). Missing entries are
    silently skipped (narrator may hallucinate a lost item that wasn't
    actually in inventory).
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack, character = cac_snap_with_character
    character.core.inventory.items.append(
        {
            "id": "narrator:rusty_compass",
            "name": "Rusty Compass",
            "description": "A corroded compass.",
            "category": "tool",
            "value": 0, "weight": 0.1, "rarity": "common",
            "narrative_weight": 0.3, "tags": [],
            "equipped": False, "quantity": 1,
            "uses_remaining": None, "state": "Carried",
        }
    )
    assert len(character.core.inventory.items) == 1

    result = NarrationTurnResult(
        narration="The merchant palms your compass.",
        items_lost=[
            {"name": "RUSTY COMPASS", "description": "lost", "category": "tool"},
        ],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack,
    )
    assert character.core.inventory.items == []


def test_items_update_tolerates_missing_characters(cac_snap) -> None:
    """Called with no character seated (e.g., pre-chargen narration), the
    inventory block must no-op without raising. Single-player saves
    populate ``snapshot.characters[0]`` before any narration turn so this
    is a safety net, not a hot path.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    assert snap.characters == []
    result = NarrationTurnResult(
        narration="Something shifts in the dust.",
        items_gained=[{"name": "Phantom Item", "description": "nil", "category": "misc"}],
    )
    # Must not raise.
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack,
    )


def test_beat_selections_apply_on_non_dice_turn(
    mw_snap_with_combat, otel_capture: InMemorySpanExporter
) -> None:
    """When ``dice_failed is None`` (no DICE_THROW this turn, e.g. a
    pure free-text narration turn while an encounter is active), the
    narrator-apply contract is unchanged — beats still land. Guards
    against accidentally dropping beats on the legacy narrator-driven
    combat path.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = mw_snap_with_combat
    result = NarrationTurnResult(
        narration="You take a swing.",
        beat_selections=[
            BeatSelection(actor="Slabgorb", beat_id="attack", target=None),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack,  # dice_failed omitted
    )
    assert snap.encounter.metric.current == 2


def test_dice_failed_none_preserves_legacy_behavior(
    mw_snap_with_combat
) -> None:
    """dice_failed=None (no roll attached to this turn) applies default
    metric_delta unconditionally — matches pre-dice-integration behavior.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = mw_snap_with_combat
    result = NarrationTurnResult(
        narration="You unleash the mutation.",
        beat_selections=[
            BeatSelection(actor="Slabgorb", beat_id="mutant_ability", target=None),
        ],
    )
    # Default call — no dice_failed kwarg. Same as every existing call site
    # before the ADR-074 wiring lands fully.
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack,
    )
    assert snap.encounter.metric.current == 4
