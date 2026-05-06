"""Story 45-21 — npc_registry HP/max_hp populated from combat stats.

Playtest 3 (Orin save, 2026-04-19): the Crawling Scavenger sat in
``snapshot.npc_registry`` with ``hp=0`` / ``max_hp=0``. Any HP-check
subsystem that queried the registry to determine creature liveness saw
"already dead" and skipped — six rounds of combat, opponent dead from
turn 0 according to the registry.

Root cause: nothing wrote combat stats into the registry entry. The
encounter handshake knew the actors and the per-side dial threshold
(= effective HP pool), but did not propagate that into the registry.

Fix: when ``instantiate_encounter_from_trigger`` builds a combat
encounter, call ``_publish_combat_stats_to_registry`` to write
``max_hp = opponent_metric.threshold`` and
``hp = max(0, threshold - current)`` into each opponent-side actor's
matching registry entry. Emits ``npc_registry.hp_set`` OTEL spans so the
GM panel lie-detector can verify the seam fired.

References:
- AC1: HP/max_hp written when combat stats are emitted
- AC2: registry entry cannot report HP=0 unless the NPC is actually dead
- AC3: OTEL span emitted with hp/max_hp values
- AC4: no regression on existing combat stats flow
- Wiring AC: production encounter init path actually calls the helper
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.agents.orchestrator import (
    NarrationTurnResult,
    NpcMention,
)
from sidequest.game.session import GameSnapshot, NpcRegistryEntry
from sidequest.game.turn import TurnManager
from sidequest.genre.loader import load_genre_pack
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from tests._helpers.session_room import room_for

_FIXTURE_PACK = Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "test_genre"


def _load_pack():
    return load_genre_pack(_FIXTURE_PACK)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def combat_snapshot():
    """Snapshot reproducing the Playtest 3 [Orin save] shape.

    Crawling Scavenger pre-registered in ``npc_registry`` with the default
    ``hp=None`` / ``max_hp=None`` (= "no combat stats published yet"); the
    next narration turn fires ``confrontation="combat"`` and the handshake
    must publish HP into the registry entry.
    """
    snap = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        turn_manager=TurnManager(interaction=4),
    )
    snap.character_locations["Orin"] = "Mawdeep Caverns"
    snap.npc_registry.append(
        NpcRegistryEntry(
            name="Crawling Scavenger",
            role="hostile",
            pronouns="it/its",
            appearance="a chittering carapaced thing the size of a hound",
            last_seen_location="Mawdeep Caverns",
            last_seen_turn=3,
        )
    )
    return snap, _load_pack()


@pytest.fixture
def otel_capture():
    """Attach an in-memory exporter to the running TracerProvider."""
    from sidequest.telemetry.setup import init_tracer

    init_tracer()
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


# ---------------------------------------------------------------------------
# Default-state contract — registry HP starts as "no claim", not zero
# ---------------------------------------------------------------------------


def test_fresh_npc_registry_entry_has_none_hp_not_zero():
    """AC2 substrate: a freshly-registered NPC has ``hp=None`` / ``max_hp=None``,
    not ``0`` / ``0``. ``None`` means "no combat stats published yet"; ``0``
    means "dead". HP-check subsystems must be able to distinguish.
    """
    entry = NpcRegistryEntry(name="Just Spawned")
    assert entry.hp is None, "fresh entry must not claim hp=0"
    assert entry.max_hp is None, "fresh entry must not claim max_hp=0"


# ---------------------------------------------------------------------------
# AC1 — HP/max_hp written on combat handshake (production wire)
# ---------------------------------------------------------------------------


def test_combat_handshake_writes_hp_into_registry_entry(combat_snapshot):
    """Playtest 3 reproduction.

    Combat encounter starts with the Crawling Scavenger pulled from the
    registry-fallback. The encounter init MUST publish HP/max_hp into the
    registry entry — otherwise HP-check subsystems read None/None forever.
    """
    snap, pack = combat_snapshot
    entry = snap.npc_registry[0]
    assert entry.hp is None, "precondition: hp not yet set"

    result = NarrationTurnResult(
        narration="The Crawling Scavenger lunges from the dark.",
        confrontation="combat",
        npcs_present=[],  # Playtest 3 shape — extraction dropped the adversary
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Orin",
        pack=pack,
        room=room_for(snap),
    )

    assert entry.max_hp is not None and entry.max_hp > 0, (
        "max_hp not populated after combat handshake"
    )
    assert entry.hp is not None and entry.hp > 0, (
        "hp not populated (or set to 0) at combat start — registry would "
        "appear always-dead like the Playtest 3 shape"
    )
    # The combat dial in test_genre has threshold=10. A fresh encounter
    # has current=starting=0, so hp == max_hp == threshold.
    assert entry.hp == entry.max_hp, "fresh combat: hp must equal max_hp (no damage taken yet)"


def test_combat_handshake_writes_hp_for_explicit_npcs_present(combat_snapshot):
    """When the narrator supplies an explicit ``npcs_present`` list, those
    opponents' registry entries also receive combat stats.

    Pre-registers a "Goblin" in the registry so the auto-register seam in
    narration_apply finds an existing entry to upsert and the handshake
    has something to write into.
    """
    snap, pack = combat_snapshot
    snap.npc_registry.append(
        NpcRegistryEntry(
            name="Goblin",
            role="hostile",
            last_seen_location="Mawdeep Caverns",
            last_seen_turn=3,
        )
    )
    result = NarrationTurnResult(
        narration="A goblin lunges.",
        confrontation="combat",
        npcs_present=[
            NpcMention(name="Goblin", side="opponent", role="hostile"),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Orin",
        pack=pack,
        room=room_for(snap),
    )
    goblin = next(e for e in snap.npc_registry if e.name == "Goblin")
    assert goblin.hp is not None and goblin.hp > 0
    assert goblin.max_hp is not None and goblin.max_hp > 0


def test_non_combat_handshake_does_not_write_hp(combat_snapshot):
    """AC2: a social/negotiation encounter has no HP semantics — the registry
    must NOT pretend to publish combat stats. Otherwise non-combat NPCs would
    suddenly have an HP claim that isn't backed by any mechanic.
    """
    snap, pack = combat_snapshot
    # Replace the hostile scavenger with a neutral entry so the handshake
    # doesn't classify it as opponent. (Negotiation isn't in the fallback
    # category-default opponent set anyway, but this keeps the test honest.)
    snap.npc_registry.clear()
    snap.npc_registry.append(
        NpcRegistryEntry(
            name="Brother Halrik",
            role="merchant",
            last_seen_location="Mawdeep Caverns",
            last_seen_turn=3,
        )
    )
    result = NarrationTurnResult(
        narration="Brother Halrik raises an eyebrow.",
        confrontation="negotiation",
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Orin",
        pack=pack,
        room=room_for(snap),
    )
    halrik = snap.npc_registry[0]
    assert halrik.hp is None, "non-combat encounter must NOT publish HP into the registry"
    assert halrik.max_hp is None


# ---------------------------------------------------------------------------
# AC3 — OTEL observability
# ---------------------------------------------------------------------------


def test_otel_span_emitted_on_registry_hp_write(combat_snapshot, otel_capture):
    """An ``npc_registry.hp_set`` span fires per registry write so the GM
    panel can verify the seam engaged. CLAUDE.md OTEL principle: every fix
    that touches a subsystem must emit OTEL so we can tell whether it
    actually engaged or Claude is just improvising.
    """
    snap, pack = combat_snapshot
    result = NarrationTurnResult(
        narration="The Crawling Scavenger lunges.",
        confrontation="combat",
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Orin",
        pack=pack,
        room=room_for(snap),
    )

    spans = [s for s in otel_capture.get_finished_spans() if s.name == "npc_registry.hp_set"]
    assert spans, (
        "no npc_registry.hp_set spans emitted — the GM panel can't verify "
        "the registry-write seam fired"
    )
    scav_span = next(
        (s for s in spans if (s.attributes or {}).get("npc_name") == "Crawling Scavenger"),
        None,
    )
    assert scav_span is not None, (
        "no hp_set span for the Crawling Scavenger — actor → registry name match regressed"
    )
    attrs = scav_span.attributes or {}
    assert int(attrs.get("hp", 0)) > 0, (
        "OTEL span carries hp=0 on a fresh combat handshake; AC3 requires "
        "hp/max_hp values be readable from the span"
    )
    assert int(attrs.get("max_hp", 0)) > 0
    assert attrs.get("source") == "encounter_handshake"


# ---------------------------------------------------------------------------
# AC4 — Regression guard: existing combat stats / handshake flow
# ---------------------------------------------------------------------------


def test_handshake_still_registers_actors_after_hp_write(combat_snapshot):
    """Smoke check that adding the registry-HP write didn't regress the
    Story 45-18 actor-registration handshake.
    """
    snap, pack = combat_snapshot
    result = NarrationTurnResult(
        narration="The Crawling Scavenger lunges.",
        confrontation="combat",
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Orin",
        pack=pack,
        room=room_for(snap),
    )
    enc = snap.encounter
    assert enc is not None
    actor_names = {a.name for a in enc.actors}
    assert {"Orin", "Crawling Scavenger"} <= actor_names


# ---------------------------------------------------------------------------
# Wiring guard — production code path actually calls the helper
# ---------------------------------------------------------------------------


def test_helper_is_called_from_production_handshake_path():
    """CLAUDE.md "Verify Wiring": the helper must be reachable from the
    production encounter init, not just the test path. ``import`` + a quick
    grep proves the seam exists; the integration test above proves it fires.
    """
    from sidequest.server.dispatch import encounter_lifecycle

    assert hasattr(encounter_lifecycle, "_publish_combat_stats_to_registry"), (
        "registry-write helper missing from production module"
    )
    src = Path(encounter_lifecycle.__file__).read_text(encoding="utf-8")
    assert "_publish_combat_stats_to_registry(" in src, (
        "helper is defined but never called — wiring regression"
    )
    # Single-call invariant: only the handshake call site exercises it.
    # Future seams (post-damage sync, on-resolution kill) should add their
    # own focused tests rather than silently piggy-backing.
    assert src.count("_publish_combat_stats_to_registry(") >= 2, (
        "helper must be both defined AND called (def + call = >=2)"
    )
