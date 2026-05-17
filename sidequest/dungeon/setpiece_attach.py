"""Set-piece attach — Plan 6, Tasks 1 & 2.

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
* ``start_trope_components`` reuses the same _slot_seed family for the
  budget-capped deterministic ordering of components (never a second scheme,
  never XOR).

Architect decisions (Plan 6, 2026-05-16)
-----------------------------------------
Decision A: origin_region + params do NOT go on TropeState (extra="ignore"
  would swallow them silently). They are carried in TropeStartResult.pending
  for Task 4's ledger thread.
Decision B: threads_lit_per_expansion is an explicit required parameter
  (no silent default, no config module). Plan 7 threads the value.

Plan 6 later tasks extend this module:
  Task 3 — quest-seed at attach
  Task 4 — ledger-add (consumes TropeStartResult.pending)
  Task 5 — resolution wiring
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any

from sidequest.dungeon.setpieces import SetPiece, TropeComponent
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
        components:               TropeComponent list from the set-piece template.
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
