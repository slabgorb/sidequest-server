"""Wire-test: container retrieved-state on room re-entry (Story 45-13).

Playtest 3 evidence (Orin, 2026-04-19): the same tin box was emptied at
round 10 and again at round 16. Identical contents both times. The
narrator's session memory is not authoritative for mechanical state
(ADR-014 / ADR-067). The fix is an explicit per-container retrieved
flag, written by ``narration_apply.py`` and read by both the
prompt-build seam (``_build_turn_context``) and the apply-time gate.

The wire-first contract demands that this file exercise three seams in
one place:

1. **Apply seam.** Drive ``_apply_narration_result_to_snapshot`` with a
   ``NarrationTurnResult`` that carries a ``from_container`` retrieval;
   assert ``snapshot.room_states[room_id].containers[container_id]``
   transitions to ``retrieved=True`` and the ``container.retrieval_recorded``
   span fires.

2. **Negative gate seam (the load-bearing block, AC #6).** Drive a
   second retrieval — same room, same container_id — and assert the
   apply-time gate filters it: items NOT appended,
   ``container.retrieval_blocked`` fires with ``prior_retrieved_at_round``
   and ``current_round``. This must hold even when the prompt-time hint
   is bypassed; that's the whole point of an apply-time gate.

3. **Prompt-build seam (AC #4).** Drive ``_build_turn_context`` and
   assert ``room.state_injected`` fires every turn — including the
   no-prior-retrievals case (``retrieved_container_count=0``), which is
   Sebastien's lie-detector requirement.

Plus AC #3 (room-scoped negative gate) and AC #5 (round-trip via
``SqliteStore``).
"""
from __future__ import annotations

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.persistence import SqliteStore
from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.genre.loader import load_genre_pack
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot

CONTENT_GENRE_PACKS = (
    __import__("pathlib").Path(__file__).resolve().parents[3]
    / "sidequest-content"
    / "genre_packs"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cac_pack():
    """Load the live caverns_and_claudes pack — wire-first wants the real
    pack, not a fixture pack, since the apply path's gating may depend on
    pack-level state. Cached by the loader."""
    return load_genre_pack(CONTENT_GENRE_PACKS / "caverns_and_claudes")


@pytest.fixture
def vault_snapshot(cac_pack):
    """A snapshot positioned in the ``mawdeep:vault`` room with a single
    PC named Rux. Round counter starts at 10 to mirror the Orin regression
    (first retrieval at round 10, second at round 16)."""
    snap = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        location="mawdeep:vault",
        discovered_rooms=["mawdeep:vault"],
        turn_manager=TurnManager(round=10, interaction=10),
    )
    char = Character(
        core=CreatureCore(
            name="Rux", description="A scavenger", personality="Cautious",
            inventory=Inventory(), statuses=[],
        ),
        char_class="Ranger", race="Human", backstory="Wanderer.",
    )
    snap.characters.append(char)
    return snap


@pytest.fixture
def otel_capture():
    """In-memory span exporter — mirrors the pattern from
    test_encounter_apply_narration.py."""
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


def _retrieval_result(
    *, container_id: str, item_name: str = "tin box contents",
) -> NarrationTurnResult:
    """Build a ``NarrationTurnResult`` whose ``items_gained`` carries a
    ``from_container`` annotation pointing at ``container_id``.

    The exact field name (``from_container`` per the story context) is
    the contract Dev (Inigo) implements in green. If Dev picks a
    different shape (e.g. a sibling ``items_from_container`` list), the
    test must be updated by the SAME PR — these tests are the contract.
    """
    return NarrationTurnResult(
        narration=f"You knock the {container_id} off the wall and pocket what's inside.",
        items_gained=[
            {
                "name": item_name,
                "description": "Whatever was in the box.",
                "category": "misc",
                "from_container": container_id,
            },
        ],
    )


# ---------------------------------------------------------------------------
# AC #1 — First retrieval records state and emits container.retrieval_recorded.
# ---------------------------------------------------------------------------


def test_first_retrieval_records_room_state_and_fires_recorded_span(
    vault_snapshot, cac_pack, otel_capture: InMemorySpanExporter,
) -> None:
    """AC #1: first retrieval of ``tin_box`` in ``mawdeep:vault``:

    - ``snapshot.room_states["mawdeep:vault"].containers["tin_box"]
       .retrieved == True``
    - ``retrieved_at_round == 10`` (matches ``turn_manager.round``)
    - The ``container.retrieval_recorded`` span fires once with the
      load-bearing attributes from the OTEL contract.
    - The item still lands in inventory — first retrieval is allowed.
    """
    result = _retrieval_result(container_id="tin_box")
    _apply_narration_result_to_snapshot(
        vault_snapshot, result, player_name="Rux", pack=cac_pack,
    )

    # Snapshot mutation
    rs = vault_snapshot.room_states["mawdeep:vault"]
    cs = rs.containers["tin_box"]
    assert cs.retrieved is True
    assert cs.retrieved_at_round == 10

    # Inventory landed (first retrieval is allowed).
    inv_names = [i["name"] for i in vault_snapshot.characters[0].core.inventory.items]
    assert "tin box contents" in inv_names

    # OTEL span
    spans = {s.name: s for s in otel_capture.get_finished_spans()}
    assert "container.retrieval_recorded" in spans, (
        f"expected container.retrieval_recorded span; got {sorted(spans)}"
    )
    span = spans["container.retrieval_recorded"]
    attrs = span.attributes or {}
    assert attrs["room_id"] == "mawdeep:vault"
    assert attrs["container_id"] == "tin_box"
    assert attrs["round_number"] == 10
    assert attrs["items_gained_count"] == 1
    assert attrs["player_name"] == "Rux"


# ---------------------------------------------------------------------------
# AC #2 — Second retrieval blocked; container.retrieval_blocked fires.
# (the Orin regression converted to a passing test)
# ---------------------------------------------------------------------------


def test_second_retrieval_same_room_same_container_is_blocked(
    vault_snapshot, cac_pack, otel_capture: InMemorySpanExporter,
) -> None:
    """AC #2 — Orin regression (rounds 10 → 16, same room, same container).

    After the first retrieval at round 10 lands, advance to round 16,
    then drive a second narrator-emitted retrieval of the same
    container. The apply-time gate must:

    - NOT append duplicate items to inventory.
    - Fire ``container.retrieval_blocked`` with
      ``prior_retrieved_at_round=10`` and ``current_round=16``.
    - Leave the existing ``room_states`` entry intact (no clobber of
      ``retrieved_at_round`` — the prior round is the load-bearing
      audit field).
    """
    # Turn 10: first retrieval.
    _apply_narration_result_to_snapshot(
        vault_snapshot,
        _retrieval_result(container_id="tin_box"),
        player_name="Rux", pack=cac_pack,
    )
    inv_after_first = list(vault_snapshot.characters[0].core.inventory.items)
    assert len(inv_after_first) == 1

    # Advance to turn 16 (round and interaction both step — the negative
    # gate uses ``turn_manager.round`` per the OTEL contract).
    vault_snapshot.turn_manager = TurnManager(round=16, interaction=16)

    # Turn 16: narrator re-emits a retrieval of the SAME container in
    # the SAME room — this is the bug Orin hit.
    second_result = _retrieval_result(
        container_id="tin_box", item_name="tin box contents",
    )
    _apply_narration_result_to_snapshot(
        vault_snapshot, second_result, player_name="Rux", pack=cac_pack,
    )

    # Inventory MUST NOT grow — the apply-time gate filtered the dup.
    inv_after_second = vault_snapshot.characters[0].core.inventory.items
    assert len(inv_after_second) == len(inv_after_first), (
        "second retrieval leaked items into inventory — apply-time gate "
        "did not block the duplicate retrieval"
    )

    # The retrieved_at_round must still reflect the FIRST retrieval —
    # the gate is a read-only check, not a clobber.
    cs = vault_snapshot.room_states["mawdeep:vault"].containers["tin_box"]
    assert cs.retrieved is True
    assert cs.retrieved_at_round == 10

    # Blocked span fires with the audit attributes.
    blocked = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "container.retrieval_blocked"
    ]
    assert len(blocked) >= 1, (
        "expected at least one container.retrieval_blocked span"
    )
    last = blocked[-1]
    attrs = last.attributes or {}
    assert attrs["room_id"] == "mawdeep:vault"
    assert attrs["container_id"] == "tin_box"
    assert attrs["prior_retrieved_at_round"] == 10
    assert attrs["current_round"] == 16
    assert attrs["player_name"] == "Rux"


# ---------------------------------------------------------------------------
# AC #3 — Negative gate is room-scoped, not global.
# ---------------------------------------------------------------------------


def test_negative_gate_is_room_scoped_not_global(
    vault_snapshot, cac_pack, otel_capture: InMemorySpanExporter,
) -> None:
    """AC #3: same container_id in two different rooms gets independent
    state. After room A retrieves ``tin_box``, the player walks into room
    B (also has a ``tin_box``). First retrieval in room B succeeds.
    """
    # Room A retrieval at round 10.
    _apply_narration_result_to_snapshot(
        vault_snapshot,
        _retrieval_result(container_id="tin_box"),
        player_name="Rux", pack=cac_pack,
    )
    assert vault_snapshot.room_states["mawdeep:vault"].containers["tin_box"].retrieved is True

    # Move to room B; bump round counter to mirror real play.
    vault_snapshot.location = "mawdeep:antechamber"
    if "mawdeep:antechamber" not in vault_snapshot.discovered_rooms:
        vault_snapshot.discovered_rooms.append("mawdeep:antechamber")
    vault_snapshot.turn_manager = TurnManager(round=11, interaction=11)

    # Same container_id, new room.
    _apply_narration_result_to_snapshot(
        vault_snapshot,
        _retrieval_result(container_id="tin_box"),
        player_name="Rux", pack=cac_pack,
    )

    # Both rooms now carry retrieved tin_box state — NOT one global flag.
    assert (
        vault_snapshot.room_states["mawdeep:vault"]
        .containers["tin_box"].retrieved is True
    )
    assert (
        vault_snapshot.room_states["mawdeep:antechamber"]
        .containers["tin_box"].retrieved is True
    )
    # The antechamber retrieval recorded the new round, not 10.
    assert (
        vault_snapshot.room_states["mawdeep:antechamber"]
        .containers["tin_box"].retrieved_at_round == 11
    )

    # No blocked-span — both are first retrievals in their respective rooms.
    blocked = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "container.retrieval_blocked"
    ]
    assert len(blocked) == 0, (
        f"expected zero blocked spans on cross-room retrieval; got {len(blocked)}"
    )


# ---------------------------------------------------------------------------
# AC #4 — room.state_injected fires on every narrator turn (including the
#         no-op case with retrieved_container_count=0).
# ---------------------------------------------------------------------------


def _build_minimal_sd(snap: GameSnapshot, pack):
    """Construct a minimal ``_SessionData`` for direct
    ``_build_turn_context`` invocation. Mirrors the pattern at
    tests/integration/test_group_c_wiring.py."""
    from unittest.mock import MagicMock

    from sidequest.game.persistence import SqliteStore
    from sidequest.server.session_handler import _SessionData

    return _SessionData(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        player_name="Rux",
        player_id="player:rux",
        snapshot=snap,
        store=SqliteStore.open_in_memory(),
        genre_pack=pack,
        orchestrator=MagicMock(),
    )


def test_room_state_injected_span_fires_with_zero_count_first_turn(
    vault_snapshot, cac_pack, otel_capture: InMemorySpanExporter,
) -> None:
    """AC #4 (no-prior-retrievals case): the FIRST narrator turn — before
    anything has been retrieved — must still fire ``room.state_injected``
    with ``retrieved_container_count=0``. That's Sebastien's
    lie-detector — without the no-op-firing case, the GM panel can't
    tell whether the gate machinery is engaged or whether the system
    just isn't bothering to look.
    """
    from sidequest.server.session_handler import _build_turn_context

    sd = _build_minimal_sd(vault_snapshot, cac_pack)
    _build_turn_context(sd)

    spans = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "room.state_injected"
    ]
    assert len(spans) >= 1, (
        "room.state_injected must fire even with no prior retrievals "
        "(Sebastien lie-detector)"
    )
    last = spans[-1]
    attrs = last.attributes or {}
    assert attrs["room_id"] == "mawdeep:vault"
    assert attrs["retrieved_container_count"] == 0


def test_room_state_injected_span_count_reflects_prior_retrievals(
    vault_snapshot, cac_pack, otel_capture: InMemorySpanExporter,
) -> None:
    """AC #4 (post-retrieval case): after a retrieval lands, the next
    ``_build_turn_context`` for the same room must fire
    ``room.state_injected`` with ``retrieved_container_count >= 1``.
    """
    from sidequest.server.session_handler import _build_turn_context

    # Land a retrieval first.
    _apply_narration_result_to_snapshot(
        vault_snapshot,
        _retrieval_result(container_id="tin_box"),
        player_name="Rux", pack=cac_pack,
    )

    # Drain prior spans so the assertion below targets the next build.
    otel_capture.clear()

    sd = _build_minimal_sd(vault_snapshot, cac_pack)
    _build_turn_context(sd)

    spans = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "room.state_injected"
    ]
    assert len(spans) >= 1
    attrs = spans[-1].attributes or {}
    assert attrs["room_id"] == "mawdeep:vault"
    assert attrs["retrieved_container_count"] == 1


def test_room_state_injected_resets_count_on_room_change(
    vault_snapshot, cac_pack, otel_capture: InMemorySpanExporter,
) -> None:
    """AC #4: the count is room-scoped — when the player walks into a
    different room with no retrievals, the span fires with
    ``retrieved_container_count=0``, NOT a stale value from the previous
    room.
    """
    from sidequest.server.session_handler import _build_turn_context

    # Land a retrieval in vault.
    _apply_narration_result_to_snapshot(
        vault_snapshot,
        _retrieval_result(container_id="tin_box"),
        player_name="Rux", pack=cac_pack,
    )

    # Move to a fresh room.
    vault_snapshot.location = "mawdeep:antechamber"
    if "mawdeep:antechamber" not in vault_snapshot.discovered_rooms:
        vault_snapshot.discovered_rooms.append("mawdeep:antechamber")

    otel_capture.clear()
    sd = _build_minimal_sd(vault_snapshot, cac_pack)
    _build_turn_context(sd)

    spans = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "room.state_injected"
    ]
    assert len(spans) >= 1
    attrs = spans[-1].attributes or {}
    assert attrs["room_id"] == "mawdeep:antechamber"
    assert attrs["retrieved_container_count"] == 0


# ---------------------------------------------------------------------------
# AC #5 — Round-trip persistence via SqliteStore.
# ---------------------------------------------------------------------------


def test_room_states_round_trip_via_sqlite_store(
    vault_snapshot, cac_pack,
) -> None:
    """AC #5: ``SqliteStore.save → load`` preserves ``room_states``."""
    # Land a retrieval so room_states has content to round-trip.
    _apply_narration_result_to_snapshot(
        vault_snapshot,
        _retrieval_result(container_id="tin_box"),
        player_name="Rux", pack=cac_pack,
    )

    store = SqliteStore.open_in_memory()
    # Initialize the slot's session_meta so the load path can reconstruct
    # SessionMeta. Mirror the pattern used by the live session handler.
    store.init_session(
        genre_slug=vault_snapshot.genre_slug,
        world_slug=vault_snapshot.world_slug,
    )
    store.save(vault_snapshot)

    loaded = store.load()
    assert loaded is not None
    rs = loaded.snapshot.room_states["mawdeep:vault"]
    cs = rs.containers["tin_box"]
    assert cs.retrieved is True
    assert cs.retrieved_at_round == 10


def test_old_save_without_room_states_loads_with_empty_default(tmp_path) -> None:
    """AC #5 forward-compat: an older save serialized BEFORE
    ``room_states`` existed must load cleanly with the field defaulting
    to ``{}``. Tested at the ``GameSnapshot.model_validate_json`` layer
    so the persistence layer doesn't need to bake in a migration step.
    """
    legacy_payload = (
        '{"genre_slug": "caverns_and_claudes", "world_slug": "mawdeep", '
        '"location": "vault"}'
    )
    snap = GameSnapshot.model_validate_json(legacy_payload)
    assert snap.room_states == {}


# ---------------------------------------------------------------------------
# AC #6 — Apply-time gate is the load-bearing block (not just the
#         prompt-time hint).
# ---------------------------------------------------------------------------


def test_apply_time_gate_blocks_when_prompt_hint_is_bypassed(
    vault_snapshot, cac_pack, otel_capture: InMemorySpanExporter,
) -> None:
    """AC #6: the prompt hint reduces leak rate; the apply-time gate
    PREVENTS leaks. Bypass the prompt-build seam entirely (don't even
    call ``_build_turn_context``) and force a duplicate retrieval
    through the apply path. The gate must still fire.

    Why this test matters: an implementation that wires the negative
    gate ONLY at prompt-build time looks correct in lab conditions but
    fails the moment the narrator decides to re-emit a retrieval anyway
    (LLMs do this). The Orin bug exists *because* implicit / soft
    gates don't survive contact with the model. The apply-time gate is
    the stop-the-leak guarantee.
    """
    # Turn 10: first retrieval, no prompt-build seam called.
    _apply_narration_result_to_snapshot(
        vault_snapshot,
        _retrieval_result(container_id="tin_box"),
        player_name="Rux", pack=cac_pack,
    )
    inv_after_first = list(vault_snapshot.characters[0].core.inventory.items)
    assert len(inv_after_first) == 1

    # Advance to turn 16. Critically: do NOT call _build_turn_context —
    # we are stubbing out the prompt-time hint entirely. The narrator
    # has been told nothing about retrieved containers.
    vault_snapshot.turn_manager = TurnManager(round=16, interaction=16)

    # Drain spans so the assertion focuses on the second turn.
    otel_capture.clear()

    # Force the duplicate retrieval through the apply path anyway.
    _apply_narration_result_to_snapshot(
        vault_snapshot,
        _retrieval_result(container_id="tin_box"),
        player_name="Rux", pack=cac_pack,
    )

    # Inventory MUST NOT grow.
    inv_after_second = vault_snapshot.characters[0].core.inventory.items
    assert len(inv_after_second) == len(inv_after_first), (
        "apply-time gate failed to block duplicate retrieval — "
        "the load-bearing block (AC #6) is not implemented"
    )

    # Blocked span fires.
    blocked = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "container.retrieval_blocked"
    ]
    assert len(blocked) == 1
    attrs = blocked[0].attributes or {}
    assert attrs["prior_retrieved_at_round"] == 10
    assert attrs["current_round"] == 16


# ---------------------------------------------------------------------------
# AC #6 supplement — items WITHOUT from_container are unaffected by the
# gate. The negative gate must not over-block.
# ---------------------------------------------------------------------------


def test_items_gained_without_from_container_pass_through_normally(
    vault_snapshot, cac_pack, otel_capture: InMemorySpanExporter,
) -> None:
    """Sanity: ``items_gained`` entries that DON'T carry a
    ``from_container`` annotation behave exactly as they did before
    45-13 (regression guard for the existing inventory wire). The gate
    must not over-block.
    """
    result = NarrationTurnResult(
        narration="The Warden falls; you scoop up the brass core.",
        items_gained=[
            {
                "name": "Brass Memory Core",
                "description": "A scavenged data spindle.",
                "category": "quest",
                # Notice: no ``from_container`` field.
            },
        ],
    )
    _apply_narration_result_to_snapshot(
        vault_snapshot, result, player_name="Rux", pack=cac_pack,
    )
    inv = vault_snapshot.characters[0].core.inventory.items
    assert len(inv) == 1
    assert inv[0]["name"] == "Brass Memory Core"

    # No container.retrieval_recorded fires (no container involved).
    recorded = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "container.retrieval_recorded"
    ]
    assert len(recorded) == 0


# ---------------------------------------------------------------------------
# Wiring sentinel — every test suite needs an integration test that
# proves the production code path actually consumes the new shape
# (CLAUDE.md: "Verify Wiring, Not Just Existence").
# ---------------------------------------------------------------------------


def test_apply_path_imports_room_state_models_for_production_code() -> None:
    """Wiring sentinel: ``narration_apply.py`` must reference the
    ``RoomState`` / ``ContainerState`` types — at the source level, not
    just in tests.

    This guards against a green-but-unwired implementation where the
    models exist on the snapshot but ``narration_apply.py`` never
    actually mutates them. A passing apply-time test could in principle
    be satisfied by a side-effect on a different code path; this static
    check fails fast if the apply file doesn't reach for the new types.
    """
    from pathlib import Path

    apply_src = Path(
        __import__("sidequest.server.narration_apply").server.narration_apply.__file__
    ).read_text(encoding="utf-8")
    # Either the model symbols are imported, or the field is read off
    # the snapshot directly. Accept any of these markers.
    has_wire = (
        "RoomState" in apply_src
        or "ContainerState" in apply_src
        or "room_states" in apply_src
        or "from_container" in apply_src
    )
    assert has_wire, (
        "narration_apply.py does not reference any 45-13 contract symbol "
        "(RoomState, ContainerState, room_states, from_container) — "
        "the apply path is not wired to the new model"
    )


def test_session_helpers_imports_room_state_for_prompt_build() -> None:
    """Wiring sentinel for the prompt-build seam: ``session_helpers.py``
    must reference ``room_states`` (or read it indirectly via the
    snapshot's already-dumped JSON). Without this, AC #4's
    ``room.state_injected`` span has nothing to read.
    """
    from pathlib import Path

    helpers_src = Path(
        __import__("sidequest.server.session_helpers").server.session_helpers.__file__
    ).read_text(encoding="utf-8")
    has_wire = (
        "room_states" in helpers_src
        or "room.state_injected" in helpers_src
        or "RoomState" in helpers_src
    )
    assert has_wire, (
        "session_helpers.py does not reference room_states or "
        "room.state_injected — the prompt-build seam is not wired"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
