"""Set-piece attach — Plan 6, Task 1: deterministic slot roll.

Public surface (first of this module's lifetimes — grows across Plan 6 Tasks
1–5, exactly like Plan 3's DepthReport precedent; NOT a stub):

    roll_set_piece(
        campaign_seed, expansion_id, region_id, setpiece_id, set_piece
    ) -> RolledSetPiece

Determinism contract
--------------------
* Pure function — no I/O, no engine mutation, no side effects.
* Identical inputs produce byte-identical results across repeated calls AND
  process restarts (frozen-into-save contract; spec §7 — once rolled, the
  result is stored and never recomputed).
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

Plan 6 later tasks extend this module:
  Task 2 — trope-start at attach
  Task 3 — quest-seed at attach
  Task 4 — ledger-add
  Task 5 — resolution wiring
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

from sidequest.dungeon.setpieces import SetPiece


@dataclass(frozen=True, eq=True)
class RolledSetPiece:
    """The result of rolling all component slots for one set-piece.

    ``slots`` maps each ComponentSlot.name to the chosen SlotOption.value.
    This is the minimal return shape needed by Plan 6 Tasks 2–5; do not
    over-design — extend in later tasks as needed.
    """

    slots: dict[str, str] = field(default_factory=dict)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RolledSetPiece):
            return NotImplemented
        return self.slots == other.slots

    def __hash__(self) -> int:
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
