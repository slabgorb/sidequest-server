"""Pure-function dice resolver — physics-is-the-roll path.

Port of sidequest-game/src/dice.rs::resolve_dice_with_faces — the server
takes a dice pool plus client-reported face values (ADR-074, story 34-12)
and produces a ResolvedRoll.

Crit semantics (locked by Keith 2026-04-11, mirrored from Rust):
- Any d20 face of 20 → CritSuccess regardless of DC / modifier
- Any d20 face of 1 → CritFail regardless of DC / modifier
- CritSuccess wins over CritFail when both appear in the same pool
- Non-d20 dice never trigger crit classification

No I/O, no wall-clock time, no global state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sidequest.protocol.dice import (
    DieGroupResult,
    DieSides,
    DieSpec,
    RollOutcome,
)

# Decisive-margin threshold: total must exceed DC by at least this many to
# crit without a nat-20. Tunable; keep aligned with BeatKind delta defaults
# (Task 6 of dual-track momentum plan).
DECISIVE_MARGIN: Final[int] = 3


class ResolveError(ValueError):
    """Raised when a dice resolution request is malformed.

    Subclasses encode the specific failure so callers can surface an
    appropriate wire error without string-matching.
    """


class EmptyPool(ResolveError):
    """Dice pool was empty."""

    def __init__(self) -> None:
        super().__init__("dice pool is empty")


class UnknownDie(ResolveError):
    """Pool contains a DieSides.Unknown — reject rather than guess."""

    def __init__(self) -> None:
        super().__init__("dice pool contains an unknown die type")


class FaceCountMismatch(ResolveError):
    """Client submitted a different number of faces than the pool contains."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"client-reported face count ({actual}) does not match pool size ({expected})"
        )
        self.expected = expected
        self.actual = actual


class FaceOutOfRange(ResolveError):
    """A client-reported face is outside ``1..=sides`` for its die."""

    def __init__(self, die_index: int, face: int, sides: int) -> None:
        super().__init__(f"die {die_index} face {face} is out of range for a d{sides}")
        self.die_index = die_index
        self.face = face
        self.sides = sides


@dataclass(frozen=True)
class ResolvedRoll:
    """Result of resolving a dice pool against a DC.

    ``rolls`` carries per-group face values paired with the originating
    ``DieSpec`` so downstream consumers (wire payload, narrator, OTEL) can
    identify which roll came from which die type.
    """

    rolls: list[DieGroupResult]
    total: int
    outcome: RollOutcome


def resolve_dice_with_faces(
    dice: list[DieSpec],
    faces: list[int],
    modifier: int,
    difficulty: int,
) -> ResolvedRoll:
    """Resolve a dice pool using client-reported face values.

    Validates:
    - Pool non-empty
    - No ``DieSides.Unknown`` in the pool
    - ``len(faces)`` equals the sum of per-group counts
    - Every face is in ``1..=sides`` for its die

    Returns a ``ResolvedRoll`` whose ``outcome`` is never
    ``RollOutcome.Unknown``. The crit rules match Rust ``resolve_dice_with_faces``
    exactly — Keith's 2026-04-11 lock.
    """
    if not dice:
        raise EmptyPool()

    for spec in dice:
        if spec.sides.faces() is None:
            raise UnknownDie()

    expected = sum(spec.count for spec in dice)
    if len(faces) != expected:
        raise FaceCountMismatch(expected=expected, actual=len(faces))

    rolls: list[DieGroupResult] = []
    face_sum = 0
    has_d20 = False
    has_d20_nat20 = False
    has_d20_nat1 = False
    flat_idx = 0

    for spec in dice:
        sides = spec.sides.faces()
        assert sides is not None  # already validated above
        group_faces: list[int] = []
        for _ in range(spec.count):
            face = faces[flat_idx]
            if face < 1 or face > sides:
                raise FaceOutOfRange(die_index=flat_idx, face=face, sides=sides)
            group_faces.append(face)
            face_sum += face

            if spec.sides is DieSides.D20:
                has_d20 = True
                if face == 20:
                    has_d20_nat20 = True
                elif face == 1:
                    has_d20_nat1 = True
            flat_idx += 1

        rolls.append(DieGroupResult(spec=spec, faces=group_faces))

    total = face_sum + modifier

    if has_d20 and has_d20_nat20:
        outcome = RollOutcome.CritSuccess
    elif has_d20 and has_d20_nat1:
        outcome = RollOutcome.CritFail
    elif total >= difficulty + DECISIVE_MARGIN:
        # Decisive-margin success — equivalent to a tabletop "succeed-with-style".
        # Required for the angle-kind two-leverage tag grant on margin alone.
        outcome = RollOutcome.CritSuccess
    elif total > difficulty:
        outcome = RollOutcome.Success
    elif total == difficulty:
        outcome = RollOutcome.Tie
    else:
        outcome = RollOutcome.Fail

    return ResolvedRoll(rolls=rolls, total=total, outcome=outcome)


def generate_dice_seed(session_id: str, round_number: int) -> int:
    """Deterministic physics seed for spectator replay.

    Mirrors Rust ``dice_dispatch::generate_dice_seed`` — combines session_id
    and round so every client in the room computes the same value for the
    same roll. Seed no longer drives the outcome on the physics-is-the-roll
    path (client faces are authoritative), but spectators still use it to
    animate the same tumble as the rolling player.
    """
    # FNV-1a 64-bit over (session_id || ':' || round). Matches the Rust
    # crate's custom hash — we don't share a binary, so any stable hash
    # that both ends compute the same works. We never re-hash on the
    # client: the client consumes the seed as an opaque u64 for Rapier.
    h = 0xcbf29ce484222325
    prime = 0x100000001b3
    mask = (1 << 64) - 1
    for byte in session_id.encode("utf-8"):
        h = ((h ^ byte) * prime) & mask
    h = ((h ^ ord(":")) * prime) & mask
    for byte in str(round_number).encode("utf-8"):
        h = ((h ^ byte) * prime) & mask
    return h
