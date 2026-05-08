"""Sealed-letter shared-world handshake — the canonical delta exchanged
between sealed-letter multiplayer turns (story 45-1, ADR-085 re-scope of
37-37).

Playtest 3 evidence (2026-04-19): Orin's narrator fabricated a "collapsed
corridor" separating Orin from Blutka because Orin's ``state_summary``
JSON had no ground truth that Blutka was in the same room. The fix is a
canonical-only delta that runs at every turn-build:

- ``build_shared_world_delta(snapshot, room=...)`` extracts the canonical
  packet (location, encounter id, party formation/adjacency).
- ``merge_shared_delta_into_snapshot(snapshot, delta)`` merges the delta
  back, emits an OTEL watcher event with resolution-path metadata, and
  returns a :class:`MergeResult` so the caller can populate span attrs.

**Canonical vs Perceived split (ADR-037, SOUL.md, AC #4):**
- Canonical (this module carries): ``location``, ``encounter_id``,
  ``party_formation`` (player_id, location, adjacency).
- Perceived (NEVER in here): mood, tactics, personality, descriptions,
  per-character POV. Perception stays with each player's narrator session.

If a perceived field ever leaks into :class:`SharedWorldDelta`, the merge
on the next player's turn will overwrite their POV state with the prior
actor's perception — bigger break than the original "collapsed corridor"
bug. Pydantic ``extra="forbid"`` is the schema-level guard.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from sidequest.game.session import GameSnapshot
from sidequest.telemetry import spans
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish


class PartyFormationEntry(BaseModel):
    """One party member's canonical placement.

    ``adjacency`` lists the other player_ids that share this entry's
    location — the narrator uses this to ground-truth "Blutka is here"
    rather than fabricating "Blutka is somewhere else."
    """

    model_config = {"extra": "forbid"}

    player_id: str
    location: str
    adjacency: list[str] = Field(default_factory=list)


class SharedWorldDelta(BaseModel):
    """Canonical shared-world packet exchanged between sealed-letter turns.

    Mutating the snapshot via this delta gives the next player's narrator
    ground-truth for adjacency. ``encounter_id`` is the active encounter's
    ``encounter_type`` (since :class:`StructuredEncounter` is keyed by
    type, not a synthetic id) — ``None`` when no encounter is live.
    """

    model_config = {"extra": "forbid"}

    location: str = ""
    encounter_id: str | None = None
    party_formation: list[PartyFormationEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class MergeResult:
    """Outcome of :func:`merge_shared_delta_into_snapshot`.

    Caller uses this to populate the OTEL span attrs (story 45-1 AC3).
    ``resolution_path`` is one of:
    - ``no_change`` — delta matched current snapshot, nothing to apply.
    - ``delta_authoritative`` — delta carried fields the snapshot didn't
      have; merged without conflict.
    - ``delta_overwrote_local`` — delta contradicted local state; delta
      won (canonical wins on the sealed-letter contract).
    """

    delta_fields: list[str]
    conflict_count: int
    resolution_path: str


def build_shared_world_delta(
    snapshot: GameSnapshot,
    *,
    room: object | None = None,
) -> SharedWorldDelta:
    """Extract the canonical handshake packet from a snapshot.

    ``room`` is a :class:`SessionRoom` (typed as ``object`` here to avoid
    a circular import). When supplied, ``slot_to_player_id`` populates
    party formation; without a room, formation is empty (solo path).

    Wave 2B (story 45-48): per-character locations come from
    ``snapshot.character_locations[name]`` via the player_seats
    (player_id → character.name) lookup. Adjacency is computed against
    seated player_ids that share the same per-character location, not
    against "every other seated player" — that's the original AC #4
    follow-up the per-character split was supposed to enable.
    """
    encounter = snapshot.encounter
    encounter_id: str | None = None
    if encounter is not None and not encounter.resolved:
        encounter_id = encounter.encounter_type

    party_formation: list[PartyFormationEntry] = []
    if room is not None:
        slot_lookup = getattr(room, "slot_to_player_id", None)
        if callable(slot_lookup):
            slot_to_pid: dict[str, str] = slot_lookup()
            seated_pids = list(slot_to_pid.values())
            # player_id → character_name for the seated PCs.
            pid_to_char: dict[str, str] = {
                pid: snapshot.player_seats.get(pid, "") for pid in seated_pids
            }
            # player_id → per-character location ("" when absent).
            pid_to_loc: dict[str, str] = {
                pid: snapshot.character_locations.get(name, "") for pid, name in pid_to_char.items()
            }
            for pid in seated_pids:
                here = pid_to_loc.get(pid, "")
                # Adjacency: seated peers whose location matches mine.
                # Empty location matches no one (an unplaced PC has no
                # canonical adjacency).
                adjacency = (
                    [
                        other
                        for other in seated_pids
                        if other != pid and pid_to_loc.get(other) == here
                    ]
                    if here
                    else []
                )
                party_formation.append(
                    PartyFormationEntry(
                        player_id=pid,
                        location=here,
                        adjacency=adjacency,
                    ),
                )

    # Party-frame consensus for the shared location field. None when
    # seated PCs disagree (split party) — represented as "" on the
    # delta because the field is typed ``str``.
    return SharedWorldDelta(
        location=snapshot.party_location() or "",
        encounter_id=encounter_id,
        party_formation=party_formation,
    )


def merge_shared_delta_into_snapshot(
    snapshot: GameSnapshot,
    delta: SharedWorldDelta,
) -> MergeResult:
    """Apply ``delta`` to ``snapshot`` and emit the handshake watcher event.

    Canonical fields (``location``, ``encounter_id``) overwrite local
    state on conflict — sealed-letter rule: the actor's emitted delta
    is authoritative for shared-world facts. Perceived fields are never
    touched (the delta has no perceived fields to carry).

    Returns a :class:`MergeResult` carrying the OTEL attrs the caller
    folds into the span. The watcher event ``game.handshake.delta_applied``
    fires regardless of resolution path so the GM panel can verify the
    merge was reached (CLAUDE.md OTEL principle: every subsystem decision
    must be visible).
    """
    delta_fields: list[str] = []
    conflict_count = 0
    resolution_path = "no_change"

    # Wave 2B (story 45-48): the canonical location field maps to
    # per-character entries in ``snapshot.character_locations`` keyed by
    # ``PartyFormationEntry.player_id`` → ``snapshot.player_seats``. The
    # legacy global ``snapshot.location`` is gone; we no longer write to
    # it. Conflict detection compares the delta's per-player location
    # against each character's prior entry.
    if delta.party_formation:
        for entry in delta.party_formation:
            if not entry.location:
                continue
            character_name = snapshot.player_seats.get(entry.player_id, "")
            if not character_name:
                continue
            existing = snapshot.character_locations.get(character_name)
            if existing and existing != entry.location:
                conflict_count += 1
                resolution_path = "delta_overwrote_local"
            elif resolution_path == "no_change":
                resolution_path = "delta_authoritative"
            snapshot.character_locations[character_name] = entry.location
        if "location" not in delta_fields:
            delta_fields.append("location")

    if delta.encounter_id is not None:
        delta_fields.append("encounter_id")
        if resolution_path == "no_change":
            resolution_path = "delta_authoritative"

    if delta.party_formation:
        delta_fields.append("party_formation")
        if resolution_path == "no_change":
            resolution_path = "delta_authoritative"

    severity = "warning" if conflict_count > 0 else "info"
    _watcher_publish(
        spans.SPAN_GAME_HANDSHAKE_DELTA_APPLIED,
        {
            "delta_fields": delta_fields,
            "conflict_count": conflict_count,
            "resolution_path": resolution_path,
        },
        component="game",
        severity=severity,
    )

    return MergeResult(
        delta_fields=delta_fields,
        conflict_count=conflict_count,
        resolution_path=resolution_path,
    )
