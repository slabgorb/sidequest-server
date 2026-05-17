"""Set-piece attach — Plan 6, Tasks 1, 2, 3 & 4.

Public surface (grows across Plan 6 Tasks 1–5, exactly like Plan 3's
DepthReport precedent; NOT a stub):

    roll_set_piece(
        campaign_seed, expansion_id, region_id, setpiece_id, set_piece
    ) -> RolledSetPiece

    start_trope_components(
        *, campaign_seed, expansion_id, region_id, setpiece_id,
        components, pack_tropes, snapshot,
        threads_lit_per_expansion, threads_already_lit
    ) -> TropeStartResult

    seed_quest_components(
        *, campaign_seed, expansion_id, region_id, setpiece_id,
        components, manifest,
        threads_lit_per_expansion, threads_already_lit
    ) -> QuestSeedResult

    attach_set_piece(
        *, campaign_seed, expansion_id, region_id, setpiece_id,
        set_piece, trope_components, quest_components,
        pack_tropes, snapshot, manifest, store,
        threads_lit_per_expansion, threads_already_lit,
        started_at_depth_score
    ) -> AttachReport

Determinism contract
--------------------
* ``roll_set_piece`` is a pure function — no I/O, no engine mutation.
* Sub-seeding uses blake2b over a pipe-delimited UTF-8 string of all five
  discriminators, fed into random.Random.  This is the canonical pattern
  established in region_graph/generator._subseed and depth.depth_jitter.
  We explicitly refuse the ``seed ^ 0x5EED`` XOR approach; that pattern has
  a fixed point at seed 24301 and must not be reproduced at any layer
  (Beneath Sünden carry-forward gotcha).
* The five-element key ``(campaign_seed|expansion_id|region_id|setpiece_id|
  slot_name)`` prevents collusion between (1,23) and (12,3) (the ``|``
  delimiter prevents naive string-concatenation aliasing) and between
  distinct slots within the same set-piece (slot_name is the innermost
  discriminator).
* ``start_trope_components`` and ``seed_quest_components`` reuse the same
  _slot_seed family for the budget-capped deterministic ordering of
  components (never a second scheme, never XOR).

Architect decisions (Plan 6, 2026-05-16)
-----------------------------------------
Decision A: origin_region + params do NOT go on TropeState (extra="ignore"
  would swallow them silently). They are carried in TropeStartResult.pending
  for Task 4's ledger thread.
Decision B: threads_lit_per_expansion is an explicit required parameter
  (no silent default, no config module). Plan 7 threads the value.
Decision C: ``seed_quest_components`` is symmetric to ``start_trope_components``
  — same budget params, same _slot_seed over-budget selection, a
  QuestSeedResult.pending list of (QuestComponent, origin_region_id) pairs
  for Task 4. The expansion budget is SHARED: Task 4's caller passes
  ``threads_already_lit = trope_result.tropes_started`` so quests consume
  what remains after tropes.

Task-3 reconciliation (Architect, 2026-05-16) — READ THIS
---------------------------------------------------------
The plan's Task 3 prose "seed into ScenarioState (ADR-053)" is SUPERSEDED.
``ScenarioState`` is a whodunit model (clue graph / guilty_npc) with no
dungeon-quest-seeding surface and is NOT touched here — no import, no
mutation. A quest component IS a future ``ComplicationThread(kind="quest")``
that Task 4 writes via Plan 5's ``DungeonStore.open_thread()``. There is no
quest registry to resolve ``quest_id`` against; "resolve quest_id" reduces
to carrying ``quest_id`` + ``params`` + origin region forward as a pending
thread (symmetric to Task 2's TropeStartResult.pending).

PLAN 7 OWNS (deferred from reduced Task 3 by Architect decision): the
set-piece↔cookbook creature/loot ref resolution of quest/slot content
against the manifest's wandering_table / loot_table. Plan 4 shipped NO
binding convention (QuestComponent.params is free-form mechanical flags;
slot values are narrative; build_wandering_table keys by canonical D&D
name; theme .refs are theme-internal). Plan 7 owns the manifest +
curation/attach + CR→Edge end-to-end (plan scope line 23), so the
content-existence join is Plan 7's by ownership. The ``manifest`` parameter
is kept here (REQUIRED — Plan 7 supplies the real RegionContentManifest so
its call shape is ready) but reduced Task 3 does NOT resolve refs against
it. Consequently reduced Task 3 has NO content-bug failure path — a
fabricated failure would be testing theater (the inverse of stubbing). See
the plan's Post-Implementation Corrections for the full decision record.

Task 4 architecture (Plan 6, 2026-05-16)
-----------------------------------------
Decision G: attach_set_piece is the single public entry point (the
  coalescence). It composes, in order: (1) roll_set_piece; (2)
  start_trope_components; (3) seed_quest_components with
  threads_already_lit = <incoming> + trope_result.tropes_started
  (tropes consume the shared budget first, quests get the remainder);
  (4) for every pending trope AND quest, build a ComplicationThread and
  call store.open_thread(thread) — Plan 5's open_thread emits ledger.add
  internally; Plan 6 does NOT emit ledger.add; (5) emit ONE
  setpiece.attach span carrying the AttachReport; (6) return AttachReport.
  This is the Plan-7 contract (Task 5 adds the mandatory wiring test).

Decision H: collision-safe, frozen-into-save thread_id. Derived
  deterministically from (campaign_seed, expansion_id, region_id,
  setpiece_id, kind, component_index) where component_index is the
  component's stable position in the deterministically-ordered pending
  list. Uses the _slot_seed/blake2b family over the pipe-delimited
  discriminator string. Never random, never XOR.

Decision I: started_at_depth_score is a REQUIRED parameter (no default —
  No Silent Fallbacks). Plan 7 owns the region/depth context and threads
  this value into attach_set_piece.

Decision J: Transaction boundary is the CALLER's (spec §7.5). attach_set_piece
  takes a store: DungeonStore parameter and calls open_thread() within
  whatever transaction Plan 7 opened. It does NOT commit and does NOT
  roll back.

Decision K: AttachReport.as_dict() is byte-pinned with a locked key set:
  {setpiece_id, region_id, tropes_started, quests_seeded, threads_written}.

Decision L: ComplicationThread.payload is the legible linkage:
  {"setpiece_id": ..., "component_index": ..., "ref_id": ..., "params": ...}.

Plan 6 later tasks extend this module:
  Task 5 — resolution wiring (DONE)

Task 5 architecture (Plan 6, 2026-05-16)
------------------------------------------
Decision M: ``resolve_complications_for_resolved_tropes`` is the Plan 6
  public resolution function. It receives the set of trope_ids that flipped
  to "resolved" this turn (the 45-20 handshake diff), finds matching open
  ``kind="trope"`` ledger threads via ``store.open_threads()`` filtered by
  ``payload["ref_id"] == resolved_trope_id``, and calls
  ``store.resolve_thread(thread_id)`` for each matching thread. Plan 5's
  ``resolve_thread`` emits ``ledger.resolve`` internally — Plan 6 does NOT
  emit ``ledger.resolve`` directly (continuation of the Seam 1 supersession:
  Plan 5 owns the span; Plan 6 only calls resolve_thread).

Decision N (STOP-AND-REPORT): ``_SessionData`` has NO ``dungeon_store``
  attribute. The real store-source seam does not exist yet — Plan 7 owns it.
  The handler-site call at the 45-20 handshake site references
  ``sd.dungeon_store`` (the Plan 7–designated attribute name) via
  ``getattr(sd, "dungeon_store", None)``. When the attribute is absent (pre-
  Plan 7), the resolution subscription logs a WARNING and skips — this is
  the honest-deferral path (NOT a silent no-op: the warning is the loud
  declaration of the missing seam). Plan 7 populates ``sd.dungeon_store`` to
  activate the path. The wiring function itself is real and fully tested.

Decision O (Plan 7 handoff): Quest-thread resolution is Plan 7's.
  ``resolve_complications_for_resolved_tropes`` resolves ONLY ``kind="trope"``
  threads. Quest threads remain ``open`` — the detection mechanic (scenario
  finish/fail) and the resolve call are Plan 7's by Architect decision.
  This is tested by Task 5 Test 2 (quest thread stays open across ticks).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any

from sidequest.dungeon.persistence import ComplicationThread, DungeonStore
from sidequest.dungeon.setpieces import QuestComponent, SetPiece, TropeComponent
from sidequest.game.session import GameSnapshot, TropeState


@dataclass(frozen=True)
class RolledSetPiece:
    """The result of rolling all component slots for one set-piece.

    ``slots`` maps each ComponentSlot.name to the chosen SlotOption.value.
    This is the minimal return shape needed by Plan 6 Tasks 2–5; do not
    over-design — extend in later tasks as needed. The ``dict``-in-frozen
    pattern is established precedent (persistence.py DungeonMutation /
    ComplicationThread); Tasks 2–5 depend on ``result.slots["name"]`` so
    do NOT change this shape in Plan 6.

    LOAD-BEARING — DO NOT "clean up":
    * ``frozen=True`` does NOT make the ``slots`` dict immutable and does
      NOT generate a working ``__hash__`` for it — a dataclass with a dict
      field raises ``TypeError: unhashable type: 'dict'`` at hash time
      unless ``__hash__`` is defined by hand. The custom ``__hash__`` below
      is REQUIRED, not redundant. Do not remove it, and do not swap
      ``slots`` to another type without updating ``__hash__`` in lockstep.
    * The custom ``__eq__`` compares by ``slots`` content with a typed
      guard (the auto-generated dataclass ``__eq__`` would also work, but
      defining ``__eq__`` by hand suppresses the auto one, which forces us
      to also define ``__hash__`` by hand — keeping both explicit makes the
      hash contract impossible to silently break).
    """

    slots: dict[str, str] = field(default_factory=dict)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RolledSetPiece):
            return NotImplemented
        return self.slots == other.slots

    def __hash__(self) -> int:
        # REQUIRED: dict fields are unhashable; frozen=True cannot auto-hash
        # this. Removing this line breaks hashing at call time. See the
        # class docstring's LOAD-BEARING note.
        return hash(tuple(sorted(self.slots.items())))


def _slot_seed(
    campaign_seed: int,
    expansion_id: int,
    region_id: str,
    setpiece_id: str,
    slot_name: str,
) -> int:
    """blake2b sub-seed for one (campaign, expansion, region, setpiece, slot) tuple.

    Mirrors region_graph.generator._subseed and depth.depth_jitter exactly:
    pipe-delimited UTF-8 string → blake2b(digest_size=8) → big-endian int.
    """
    digest = hashlib.blake2b(
        f"{campaign_seed}|{expansion_id}|{region_id}|{setpiece_id}|{slot_name}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big")


def roll_set_piece(
    *,
    campaign_seed: int,
    expansion_id: int,
    region_id: str,
    setpiece_id: str,
    set_piece: SetPiece,
) -> RolledSetPiece:
    """Roll each ComponentSlot of *set_piece* to exactly one SlotOption.

    Args:
        campaign_seed: Integer seed for this dungeon campaign.
        expansion_id:  Integer expansion id (matches RegionNode.expansion_id).
        region_id:     String region id (matches RegionNode.id).
        setpiece_id:   String id of the set-piece template (matches SetPiece.id).
        set_piece:     The validated SetPiece template.  Every slot is guaranteed
                       ≥1 option by Plan 4's validator; this function asserts
                       that invariant rather than re-validating.

    Returns:
        A frozen RolledSetPiece mapping each slot name to its chosen option value.

    Raises:
        AssertionError: if a slot has zero options (violates Plan 4's invariant;
                        loud failure per CLAUDE.md "No Silent Fallbacks").
    """
    rolled: dict[str, str] = {}
    for slot in set_piece.slots:
        # Plan 4's validator guarantees len >= 1; assert the invariant loudly.
        assert slot.options, (
            f"ComponentSlot {slot.name!r} has no options — "
            "Plan 4's validator should have rejected this set-piece"
        )
        seed = _slot_seed(campaign_seed, expansion_id, region_id, setpiece_id, slot.name)
        rng = random.Random(seed)
        chosen = rng.choices(slot.options, weights=[o.weight for o in slot.options], k=1)[0]
        rolled[slot.name] = chosen.value

    return RolledSetPiece(slots=rolled)


# ---------------------------------------------------------------------------
# Task 2: Trope-component start → live trope engine (ADR-018 seam)
# ---------------------------------------------------------------------------


@dataclass
class TropeStartResult:
    """Result of start_trope_components.

    ``tropes_started`` is the count added to snapshot.active_tropes this
    call.  Task 4 adds that value to the running threads_already_lit total.

    ``pending`` carries (component, origin_region_id) pairs for every trope
    that was started — Task 4 writes these to the ledger as ComplicationThread
    entries (origin_region_id + component.params are thread provenance;
    they CANNOT go on TropeState because extra="ignore" would swallow them
    silently — Decision A).
    """

    tropes_started: int
    pending: list[tuple[TropeComponent, str]] = field(default_factory=list)


def start_trope_components(
    *,
    campaign_seed: int,
    expansion_id: int,
    region_id: str,
    setpiece_id: str,
    components: list[TropeComponent],
    pack_tropes: Any,
    snapshot: GameSnapshot,
    threads_lit_per_expansion: int,
    threads_already_lit: int,
) -> TropeStartResult:
    """Start each TropeComponent: resolve against pack, append TropeState,
    emit trope.start span, return TropeStartResult for Task 4's ledger.

    Args:
        campaign_seed:            Campaign-level integer seed.
        expansion_id:             Expansion id (matches RegionNode.expansion_id).
        region_id:                Origin region id — carried in pending for
                                  Task 4; NOT stored on TropeState (Decision A).
        setpiece_id:              Set-piece id (used for seed discrimination).
        components:               TropeComponent list from the set-piece
                                  template. ORDER IS LOAD-BEARING: the
                                  over-budget selection sub-seeds on the
                                  enumerate() index ("trope_order|<idx>"), so
                                  if the caller reorders this list the seeded
                                  selection silently changes and a re-attach
                                  no longer matches the frozen save (spec §7).
                                  Pass it in the set-piece template's authored
                                  order; do not sort/filter upstream. Pinned by
                                  test_trope_over_budget_selection_against_hardcoded_expected_value.
        pack_tropes:              Duck-typed pack object with a .tropes attribute
                                  (list of TropeDefinition-like objects with .id).
                                  Same duck type tick_tropes uses.
        snapshot:                 Mutable GameSnapshot — active_tropes is
                                  mutated in place (TropeState appended).
        threads_lit_per_expansion: Required budget. No silent default (No Silent
                                  Fallbacks). Plan 7 threads this value.
        threads_already_lit:      Count already consumed this expansion (shared
                                  with Task 3's quest components). Remaining
                                  budget = threads_lit_per_expansion -
                                  threads_already_lit.

    Returns:
        TropeStartResult with count lit and pending (component, region_id)
        pairs for Task 4.

    Raises:
        ValueError: if ANY component's trope_id (not just the budget-selected
                    ones) is not present in pack_tropes.  This is a content
                    authoring bug — loud failure per CLAUDE.md "No Silent
                    Fallbacks", and the rejection is ATOMIC: validation runs
                    over the whole components list BEFORE any
                    ``snapshot.active_tropes.append``, so a bad trope_id
                    rejects the entire set-piece's trope-start with zero
                    snapshot mutation (no orphan TropeState on raise — Task 5
                    wires this into a live snapshot). The trope.start span
                    is still emitted with failed=True before the exception
                    propagates so the GM panel sees the content bug.
    """
    from sidequest.telemetry.spans.dungeon_setpiece import trope_start_span

    # Build the trope resolution map — same approach as tick_tropes.
    pack_tropes_by_id: dict[str, Any] = {
        t.id: t for t in getattr(pack_tropes, "tropes", []) if t.id is not None
    }

    remaining = threads_lit_per_expansion - threads_already_lit
    if remaining <= 0 or not components:
        return TropeStartResult(tropes_started=0)

    # PASS 1 — validate EVERY component's trope_id against the pack BEFORE
    # any snapshot mutation. A bad trope_id in an authored set-piece is a
    # content bug; it must reject the whole set-piece cleanly, not leave
    # an orphan TropeState behind when the exception propagates ("No Silent
    # Fallbacks" covers state consistency, not just error surfacing). The
    # failure path still emits a trope.start span with failed=True so the
    # GM panel sees the content bug.
    for component in components:
        if component.trope_id not in pack_tropes_by_id:
            with trope_start_span(
                trope_id=component.trope_id,
                setpiece_id=setpiece_id,
                origin_region_id=region_id,
            ) as span:
                span.set_attribute("failed", True)
                raise ValueError(
                    f"trope_id {component.trope_id!r} not found in pack — "
                    "content authoring bug (add it to tropes.yaml or fix the "
                    "set-piece template). No Silent Fallbacks."
                )

    # Deterministic ordering of components within this budget cap.
    # Reuses the _slot_seed / blake2b family — index as the innermost
    # discriminator (canonical pattern, no second seed scheme, no XOR).
    indexed = list(enumerate(components))
    indexed.sort(
        key=lambda t: _slot_seed(
            campaign_seed,
            expansion_id,
            region_id,
            setpiece_id,
            f"trope_order|{t[0]}",
        )
    )
    selected = indexed[:remaining]

    pending: list[tuple[TropeComponent, str]] = []
    started = 0

    # PASS 2 — all ids validated above; append + emit the success span.
    for _orig_idx, component in selected:
        with trope_start_span(
            trope_id=component.trope_id,
            setpiece_id=setpiece_id,
            origin_region_id=region_id,
        ):
            # Task 4 note: do NOT derive thread_id from trope_id alone.
            # Two TropeComponents with the same trope_id in one set-piece
            # are intentionally allowed (each lights its own TropeState);
            # a trope_id-only thread_id would collide and trip Plan 5's
            # open_thread duplicate-thread_id loud raise. Use a per-component
            # discriminator (origin region + component index / params).
            snapshot.active_tropes.append(
                TropeState(id=component.trope_id, status="progressing", progress=0.0)
            )
            pending.append((component, region_id))
            started += 1

    return TropeStartResult(tropes_started=started, pending=pending)


# ---------------------------------------------------------------------------
# Task 3: Quest-component seed → pending ComplicationThread(kind="quest")
#
# REDUCED SCOPE (Architect decision 2026-05-16). The plan's Task 3 prose
# "seed into ScenarioState (ADR-053)" is SUPERSEDED — ScenarioState is a
# whodunit model with no dungeon-quest surface and is NOT touched (see the
# module docstring's Task-3 reconciliation note). A quest component is a
# future ComplicationThread(kind="quest") that Task 4 persists via Plan 5's
# DungeonStore.open_thread(). There is no quest registry to resolve
# quest_id against; "seed a quest" reduces to carrying quest_id + params +
# origin region forward as a pending thread, symmetric to Task 2.
#
# The set-piece↔cookbook creature/loot manifest-join is REASSIGNED TO PLAN 7
# (Plan 4 shipped no ref convention; Plan 7 owns the manifest + CR→Edge
# end-to-end). The `manifest` parameter is kept (Plan 7 supplies the real
# RegionContentManifest so its call shape is ready) but reduced Task 3 does
# NOT resolve refs against it. There is therefore NO content-bug failure
# path here — a fabricated failure would be testing theater. See the plan's
# Post-Implementation Corrections for the full decision record.
# ---------------------------------------------------------------------------


@dataclass
class QuestSeedResult:
    """Result of seed_quest_components — symmetric to TropeStartResult.

    ``quests_seeded`` is the count of quest threads seeded this call. Task 4
    adds it to the running threads_already_lit total exactly as it does for
    TropeStartResult.tropes_started (shared expansion budget).

    ``pending`` carries (component, origin_region_id) pairs for every quest
    seeded — Task 4 writes these to the ledger as
    ``ComplicationThread(kind="quest", origin_region_id=<region>,
    payload={"quest_id": ..., "params": ...})`` via Plan 5's
    ``DungeonStore.open_thread()``. quest_id + params are thread provenance
    carried here (not on any ScenarioState — that path is superseded;
    see the module docstring).
    """

    quests_seeded: int
    pending: list[tuple[QuestComponent, str]] = field(default_factory=list)


def seed_quest_components(
    *,
    campaign_seed: int,
    expansion_id: int,
    region_id: str,
    setpiece_id: str,
    components: list[QuestComponent],
    manifest: Any,
    threads_lit_per_expansion: int,
    threads_already_lit: int,
) -> QuestSeedResult:
    """Seed each QuestComponent as a pending ComplicationThread(kind="quest").

    Symmetric to ``start_trope_components``: budget-capped, deterministic
    over-budget selection via the shared ``_slot_seed`` / blake2b family,
    one ``quest.seed`` span per seeded component, a QuestSeedResult.pending
    list of (component, origin_region_id) pairs for Task 4's ledger.

    Args:
        campaign_seed:            Campaign-level integer seed.
        expansion_id:             Expansion id (matches RegionNode.expansion_id).
        region_id:                Origin region id — carried in pending for
                                  Task 4 (becomes
                                  ComplicationThread.origin_region_id).
        setpiece_id:              Set-piece id (used for seed discrimination).
        components:               QuestComponent list from the set-piece
                                  template. ORDER IS LOAD-BEARING: the
                                  over-budget selection sub-seeds on the
                                  enumerate() index ("quest_order|<idx>"), so
                                  if the caller reorders this list the seeded
                                  selection silently changes and a re-attach
                                  no longer matches the frozen save (spec §7).
                                  Pass it in the set-piece template's authored
                                  order; do not sort/filter upstream. Pinned by
                                  test_quest_over_budget_selection_against_hardcoded_expected_value.
        manifest:                 The RegionContentManifest Plan 7 supplies
                                  (duck-typed on .wandering_table /
                                  .loot_table, mirroring Task 2's
                                  ``pack_tropes: Any`` precedent). REQUIRED so
                                  Plan 7's call shape is ready. Reduced Task 3
                                  does NOT resolve refs against it — the
                                  creature/loot manifest-join is Plan 7's by
                                  Architect decision (Plan 4 shipped no ref
                                  convention; see the module docstring and the
                                  plan's Post-Implementation Corrections).
        threads_lit_per_expansion: Required budget. No silent default (No
                                  Silent Fallbacks). Plan 7 threads this value.
        threads_already_lit:      Count already consumed this expansion —
                                  SHARED with Task 2's tropes. Task 4's caller
                                  passes
                                  ``threads_already_lit = trope_result.tropes_started``
                                  so quests consume what remains after tropes.
                                  Remaining = threads_lit_per_expansion -
                                  threads_already_lit.

    Returns:
        QuestSeedResult with the count seeded and pending (component,
        region_id) pairs for Task 4.

    Raises:
        Nothing by design. Reduced Task 3 has NO content-bug failure path:
        there is no quest registry to resolve quest_id against, and the
        creature/loot manifest-join that could surface a content bug is
        Plan 7's (Plan 4 shipped no ref convention). A fabricated failure
        mode just to have a failure test would be testing theater — the
        inverse of stubbing. ``quest.seed`` is an informational/success span.
    """
    from sidequest.telemetry.spans.dungeon_setpiece import quest_seed_span

    remaining = threads_lit_per_expansion - threads_already_lit
    if remaining <= 0 or not components:
        return QuestSeedResult(quests_seeded=0)

    # NOTE (atomicity, Decision D): start_trope_components runs a
    # validate-all PASS 1 before mutating because an unknown trope_id is a
    # content bug that must reject the whole set-piece atomically. Reduced
    # Task 3 has NO such trigger (no quest registry; manifest-join is
    # Plan 7's), so there is nothing to validate-all here. The structural
    # symmetry is preserved (budget gate → deterministic select → emit), but
    # PASS 1 is intentionally absent because inventing a check just to mirror
    # the shape would be dead code. If Plan 7 ever pushes ref-resolution back
    # into Plan 6, reinstate a PASS 1 validate-all here (same two-pass
    # discipline as start_trope_components).

    # Deterministic ordering of components within this budget cap. Reuses the
    # _slot_seed / blake2b family — index as the innermost discriminator
    # (canonical pattern, no second seed scheme, no XOR). The "quest_order|"
    # prefix keeps quest selection independent of trope selection even when a
    # set-piece's trope and quest component lists are the same length.
    indexed = list(enumerate(components))
    indexed.sort(
        key=lambda t: _slot_seed(
            campaign_seed,
            expansion_id,
            region_id,
            setpiece_id,
            f"quest_order|{t[0]}",
        )
    )
    selected = indexed[:remaining]

    pending: list[tuple[QuestComponent, str]] = []
    seeded = 0

    for _orig_idx, component in selected:
        with quest_seed_span(
            quest_id=component.quest_id,
            setpiece_id=setpiece_id,
            origin_region_id=region_id,
        ):
            # Task 4 note: do NOT derive thread_id from quest_id alone — two
            # QuestComponents with the same quest_id in one set-piece each
            # seed their own thread; a quest_id-only thread_id would collide
            # and trip Plan 5's open_thread duplicate-thread_id loud raise.
            # Use a per-component discriminator (origin region + component
            # index / params), exactly as the Task 2 trope note instructs.
            # This duplicate-id behavior is PINNED by
            # tests/dungeon/test_setpiece_attach.py::
            # test_duplicate_quest_id_in_one_setpiece_seeds_two_pending
            # (symmetric to the trope version) — read it before wiring Task 4.
            pending.append((component, region_id))
            seeded += 1

    return QuestSeedResult(quests_seeded=seeded, pending=pending)


# ---------------------------------------------------------------------------
# Task 4: Ledger add — every started thread persisted (Plan 5 seam)
#
# CRITICAL SUPERSESSION (Architect Task-0 reconciliation):
# Plan 5's DungeonStore.open_thread() ALREADY emits ledger.add internally.
# Plan 6 does NOT emit ledger.add. Task 4 emits ONLY setpiece.attach and
# calls open_thread() (which emits ledger.add itself).
# ---------------------------------------------------------------------------


@dataclass
class AttachReport:
    """Structured attach output.

    TWO distinct surfaces, deliberately NOT the same shape:

    * ``as_dict()`` — the byte-pinned, key-LOCKED ``setpiece.attach`` /
      Plan-7 ``attach``-span OTEL contract (Decision K). EXACTLY the five
      flat scalar keys ``{setpiece_id, region_id, tropes_started,
      quests_seeded, threads_written}`` — mirrors GenerationReport.as_dict()
      (invariants.py) and DepthReport.as_dict() (depth.py). ``rolled`` is
      NOT in ``as_dict()``: a RolledSetPiece is a nested structure, not a
      flat OTEL span attribute, and adding it would pollute the locked span
      contract and break the GM panel. Any addition/removal here breaks
      Plan 7's attach span — pinned by
      test_attach_report_as_dict_key_set_locked.

    * ``.rolled`` — the deterministic RolledSetPiece for these inputs.
      spec §7 requires Plan 7 to FREEZE the exact rolled result into the
      save and NEVER recompute it; if Task 4 discarded it Plan 7 could not
      freeze it. This field carries it out so Plan 7's commit can persist
      it. Read off the report object directly — NOT via ``as_dict()``.
    """

    setpiece_id: str
    region_id: str
    tropes_started: int
    quests_seeded: int
    threads_written: int  # = tropes_started + quests_seeded
    # spec §7 freeze target — NOT in as_dict(). default_factory mirrors the
    # DepthReport/GenerationReport plain-dataclass-with-defaults precedent;
    # attach_set_piece ALWAYS supplies the real rolled value (production never
    # relies on the empty default — it is a data-carrier construction
    # convenience, not a silent fallback in any live code path).
    rolled: RolledSetPiece = field(default_factory=RolledSetPiece)

    def as_dict(self) -> dict:
        """The LOCKED flat OTEL span contract (Decision K).

        EXACTLY the five scalar keys — ``rolled`` is intentionally absent
        (it is a nested structure Plan 7 freezes off ``.rolled``, not a
        flat span attribute). Mirrors DepthReport.as_dict() precisely.
        """
        return {
            "setpiece_id": self.setpiece_id,
            "region_id": self.region_id,
            "tropes_started": self.tropes_started,
            "quests_seeded": self.quests_seeded,
            "threads_written": self.threads_written,
        }


def _thread_id_seed(
    campaign_seed: int,
    expansion_id: int,
    region_id: str,
    setpiece_id: str,
    kind: str,
    component_index: int,
) -> str:
    """Deterministic, collision-safe thread_id for one pending component.

    Decision H: derived from (campaign_seed, expansion_id, region_id,
    setpiece_id, kind, component_index) using the _slot_seed/blake2b family
    over a pipe-delimited UTF-8 string. component_index is the component's
    stable position in the deterministically-ordered pending list — unique
    even across duplicate trope_id/quest_id values within one set-piece
    (Decision H). Never random, never XOR.

    A genuine re-attach of the same set-piece with the same component_index
    produces the SAME thread_id — correctly tripping Plan 5's open_thread
    duplicate-thread_id loud raise (the desired freeze-violation signal).
    """
    digest = hashlib.blake2b(
        f"{campaign_seed}|{expansion_id}|{region_id}|{setpiece_id}|{kind}|{component_index}".encode(),
        digest_size=8,
    ).digest()
    hex_val = digest.hex()
    return f"thread|{kind}|{campaign_seed}|{expansion_id}|{region_id}|{setpiece_id}|{component_index}|{hex_val}"


def attach_set_piece(
    *,
    campaign_seed: int,
    expansion_id: int,
    region_id: str,
    setpiece_id: str,
    set_piece: SetPiece,
    trope_components: list[TropeComponent],
    quest_components: list[QuestComponent],
    pack_tropes: Any,
    snapshot: GameSnapshot,
    manifest: Any,
    store: DungeonStore,
    threads_lit_per_expansion: int,
    threads_already_lit: int,
    started_at_depth_score: float,
) -> AttachReport:
    """The single public coalescence entry point for Plan 7's attach stage.

    Composes, in order (Decision G):
      1. roll_set_piece — roll all component slots.
      2. start_trope_components — start trope components up to budget.
      3. seed_quest_components — seed quest components from remaining budget
         (threads_already_lit = incoming + trope_result.tropes_started so
         tropes consume the shared expansion budget first).
      4. For every pending trope AND every pending quest, build a
         ComplicationThread and call store.open_thread(thread). Plan 5's
         open_thread emits ledger.add internally — Plan 6 does NOT emit
         ledger.add directly (critical supersession, Architect Task-0).
      5. Emit ONE setpiece.attach span carrying AttachReport fields.
      6. Return an AttachReport.

    Args:
        campaign_seed:             Campaign-level integer seed.
        expansion_id:              Expansion id (matches RegionNode.expansion_id).
        region_id:                 Origin region id.
        setpiece_id:               Set-piece template id.
        set_piece:                 Validated SetPiece template.
        trope_components:          TropeComponent list from the set-piece template.
                                   ORDER IS LOAD-BEARING (see start_trope_components).
        quest_components:          QuestComponent list from the set-piece template.
                                   ORDER IS LOAD-BEARING (see seed_quest_components).
        pack_tropes:               Duck-typed pack object with .tropes attribute.
        snapshot:                  Mutable GameSnapshot — active_tropes mutated.
        manifest:                  RegionContentManifest (Plan 7 supplies the real
                                   one; reduced Task 3 does NOT resolve refs against
                                   it — the creature/loot join is Plan 7's).
        store:                     Real DungeonStore bound to the caller's connection
                                   (Plan 7 owns the connection; spec §7.5). NOT
                                   duck-typed as Any — concrete Plan 5 dependency
                                   (Decision J). attach_set_piece does NOT commit
                                   and does NOT roll back; caller owns the txn.
        threads_lit_per_expansion: Required expansion-level thread budget. No
                                   silent default (No Silent Fallbacks, Decision B).
        threads_already_lit:       Count already consumed this expansion before
                                   this set-piece. attach_set_piece adds
                                   trope_result.tropes_started to this before
                                   passing to seed_quest_components (Decision C).
        started_at_depth_score:    Depth score of the region this set-piece
                                   attaches at. REQUIRED — no default (No Silent
                                   Fallbacks, Decision I). Plan 7 owns the
                                   region/depth context and threads this value.

    Returns:
        AttachReport. ``as_dict()`` is the locked flat span contract
        (Decision K): setpiece_id, region_id, tropes_started, quests_seeded,
        threads_written. ``report.rolled`` carries the deterministic
        RolledSetPiece Plan 7 freezes into the save (spec §7, never
        recomputed) — read off the object, NOT via as_dict().

    Raises:
        ValueError: if any TropeComponent.trope_id is not in pack_tropes
                    (content authoring bug — start_trope_components raises
                    loudly with a trope.start(failed=True) span).
        PersistError: if a duplicate thread_id is written. A genuine
                      re-attach with identical inputs on the same store
                      raises PersistError on the FIRST duplicate thread_id
                      (trope component_index 0) BEFORE any new rows land —
                      partial writes cannot occur (Decision J: the caller
                      owns the txn; re-attach is the caller's mistake and
                      the loud raise is the spec §7 freeze-violation signal,
                      NOT swallowed).

    This is the Plan-7 contract. Task 5 adds the mandatory wiring test
    binding to this signature.
    """
    from sidequest.telemetry.spans.dungeon_setpiece import setpiece_attach_span  # noqa: PLC0415

    # Step 1: Roll all component slots. The RolledSetPiece is the
    # deterministic attach output spec §7 requires Plan 7 to FREEZE into
    # the save and NEVER recompute — it is carried out on AttachReport.rolled
    # (NOT discarded; NOT in as_dict() — see AttachReport docstring).
    rolled = roll_set_piece(
        campaign_seed=campaign_seed,
        expansion_id=expansion_id,
        region_id=region_id,
        setpiece_id=setpiece_id,
        set_piece=set_piece,
    )

    # Step 2: Start trope components (validates all trope_ids first — atomic).
    trope_result = start_trope_components(
        campaign_seed=campaign_seed,
        expansion_id=expansion_id,
        region_id=region_id,
        setpiece_id=setpiece_id,
        components=trope_components,
        pack_tropes=pack_tropes,
        snapshot=snapshot,
        threads_lit_per_expansion=threads_lit_per_expansion,
        threads_already_lit=threads_already_lit,
    )

    # Step 3: Seed quest components — tropes consumed the budget first.
    quest_result = seed_quest_components(
        campaign_seed=campaign_seed,
        expansion_id=expansion_id,
        region_id=region_id,
        setpiece_id=setpiece_id,
        components=quest_components,
        manifest=manifest,
        threads_lit_per_expansion=threads_lit_per_expansion,
        threads_already_lit=threads_already_lit + trope_result.tropes_started,
    )

    # Step 4: Write one ComplicationThread per pending entry into the ledger
    # (within the caller's transaction — no autocommit, Decision J).
    # thread_id uses a per-component discriminator from the pending list
    # position (component_index) for collision-safety (Decision H).
    # Plan 5's open_thread() emits ledger.add internally — Plan 6 does NOT
    # emit ledger.add (critical supersession, Architect Task-0 reconciliation).
    threads_written = 0

    for component_index, (component, origin_region_id) in enumerate(trope_result.pending):
        thread_id = _thread_id_seed(
            campaign_seed,
            expansion_id,
            region_id,
            setpiece_id,
            "trope",
            component_index,
        )
        thread = ComplicationThread(
            thread_id=thread_id,
            origin_region_id=origin_region_id,
            kind="trope",
            status="open",
            started_at_depth_score=started_at_depth_score,
            payload={
                "setpiece_id": setpiece_id,
                "component_index": component_index,
                "ref_id": component.trope_id,
                "params": component.params,
            },
        )
        store.open_thread(thread)
        threads_written += 1

    # Quest pending: component_index restarts at 0 (enumerate over the
    # quest pending list). A trope and a quest at the SAME list position
    # still get distinct thread_ids because _thread_id_seed mixes the
    # kind discriminator ("trope" vs "quest") into the blake2b input —
    # there is NO numeric offset; the kind field is what separates them.
    for component_index, (component, origin_region_id) in enumerate(quest_result.pending):
        thread_id = _thread_id_seed(
            campaign_seed,
            expansion_id,
            region_id,
            setpiece_id,
            "quest",
            component_index,
        )
        thread = ComplicationThread(
            thread_id=thread_id,
            origin_region_id=origin_region_id,
            kind="quest",
            status="open",
            started_at_depth_score=started_at_depth_score,
            payload={
                "setpiece_id": setpiece_id,
                "component_index": component_index,
                "ref_id": component.quest_id,
                "params": component.params,
            },
        )
        store.open_thread(thread)
        threads_written += 1

    # Step 5: Emit ONE setpiece.attach span carrying the AttachReport contract
    # fields. This is the single lie-detector span Plan 7 reads from the GM
    # panel to confirm the attach completed and how many threads were written.
    report = AttachReport(
        setpiece_id=setpiece_id,
        region_id=region_id,
        tropes_started=trope_result.tropes_started,
        quests_seeded=quest_result.quests_seeded,
        threads_written=threads_written,
        rolled=rolled,  # spec §7 freeze target — Plan 7 persists this
    )
    # report.as_dict() is the LOCKED 5-key flat span contract — rolled is
    # intentionally absent from it (a nested struct is not a flat OTEL attr).
    with setpiece_attach_span(**report.as_dict()):
        pass

    # Step 6: Return the AttachReport (Plan 7's attach-stage contract).
    return report


# ---------------------------------------------------------------------------
# Task 5: Resolution wiring — subscribe to the existing 45-20 handshake diff
# ---------------------------------------------------------------------------


def resolve_complications_for_resolved_tropes(
    *,
    resolved_trope_ids: list[str],
    store: DungeonStore,
) -> None:
    """For each trope_id that flipped to "resolved" this turn, find and
    resolve matching open ``kind="trope"`` ledger threads via Plan 5's
    ``store.resolve_thread()``.

    Decision M: this function is called from the 45-20 handshake site in
    ``websocket_session_handler.py`` with the diff that site already
    computes (the set of trope_ids whose status flipped to "resolved" this
    turn — do NOT recompute; reuse the handshake's detection). Plan 5's
    ``resolve_thread`` emits ``ledger.resolve`` internally — Plan 6 does
    NOT emit ``ledger.resolve`` directly (Seam 1 supersession continuation).

    Decision A reminder: a TropeState carries only ``id`` + ``status``, no
    thread_id back-reference. The mapping is by matching open
    ``kind="trope"`` threads whose ``payload["ref_id"] == resolved_trope_id``.
    Duplicate trope_ids in one set-piece → multiple threads with the same
    ref_id; each matching open thread is resolved (one per resolution event,
    aggregate per spec §7.1).

    Decision O: quest-thread resolution is Plan 7's. This function resolves
    ONLY ``kind="trope"`` threads. Quest threads remain open.

    Args:
        resolved_trope_ids: List of trope_ids that flipped to "resolved"
            this turn (produced by the 45-20 handshake diff in
            ``websocket_session_handler.py``). Empty list is a valid no-op
            (nothing resolved this turn).
        store: Real DungeonStore from Plan 7's session seam. The caller
            (the 45-20 handshake site) obtains this from ``sd.dungeon_store``
            (the Plan 7–designated attribute). Transaction boundary is the
            caller's (plan §7.5).

    Raises:
        NotFoundError: propagated from ``store.resolve_thread`` if a
            matched thread_id is absent from the ledger — this is a real
            bug (the match logic found a thread, then it disappeared), NOT
            silenced.
    """
    if not resolved_trope_ids:
        return

    # Fetch all open threads once — O(open_threads) rather than one query
    # per trope_id. On typical dungeon sizes (tens to hundreds of threads)
    # this is fast; if the ledger grows very large Plan 7 can add an index
    # on payload["ref_id"] without changing this interface.
    open_threads = store.open_threads()

    # Build a lookup: ref_id → list[thread_id] for open trope threads.
    # Multiple threads with the same ref_id are all included (duplicate
    # trope_id case, spec §7.1 aggregate resolution).
    trope_threads_by_ref_id: dict[str, list[str]] = {}
    for thread in open_threads:
        if thread.kind != "trope":
            # Decision O: only trope threads; quest resolution is Plan 7's.
            continue
        ref_id = thread.payload.get("ref_id", "")
        if ref_id not in trope_threads_by_ref_id:
            trope_threads_by_ref_id[ref_id] = []
        trope_threads_by_ref_id[ref_id].append(thread.thread_id)

    for trope_id in resolved_trope_ids:
        for thread_id in trope_threads_by_ref_id.get(trope_id, []):
            # Plan 5's resolve_thread emits ledger.resolve internally — Plan 6
            # does NOT add another emit here (Seam 1 supersession continuation).
            # NotFoundError propagates — the match above found this thread_id,
            # so absence at resolve time is a real bug (No Silent Fallbacks).
            store.resolve_thread(thread_id)
