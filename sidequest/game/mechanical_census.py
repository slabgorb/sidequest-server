"""Phase 2 forensics: pure projections of canonical mechanical state.

Every accessor here was confirmed from source 2026-05-18 (see the plan's
Spec Reconciliation table R3-R9). These functions are PURE (no I/O, never
raise) EXCEPT emit_mechanical_census, which calls publish_event and is
fully wrapped so telemetry never crashes a turn (Phase 1 contract).
"""
from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)


def inventory_digest(items: list) -> list[dict]:
    """Fold raw inventory entries into [{item, qty}], aggregated by name.

    R7: items is list[dict]; narrator dups arrive as quantity:1 singletons,
    so we MUST sum by name, not trust per-entry quantity. Nameless entries
    are loud-skipped (never silently dropped). Output is name-sorted for a
    stable diff key."""
    agg: dict[str, int] = {}
    for entry in items or []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not name:
            logger.warning(
                "mechanical_census.inventory_unnamed_entry entry=%r", entry
            )
            continue
        qty = entry.get("quantity", 1)
        if not isinstance(qty, int):
            qty = 1
        agg[name] = agg.get(name, 0) + qty
    return [{"item": n, "qty": agg[n]} for n in sorted(agg)]


def inv_hash(items: list) -> str:
    """Stable 16-hex digest of the aggregated inventory (cheap diff key)."""
    digest = inventory_digest(items)
    blob = json.dumps(digest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def seat_index(room, player_id: str) -> int:
    """Best-effort positional seat (R9): index in playing_player_ids().
    Not durable across reconnect; player_id is the real key. Never raises
    — absent / no room -> -1 (honest sentinel, not a silent 0)."""
    try:
        return list(room.playing_player_ids()).index(player_id)
    except (AttributeError, ValueError, TypeError):
        return -1


def build_pc_census(
    *,
    character,
    player_id: str,
    character_name: str,
    seat: int,
    round_number: int,
    location,
) -> dict:
    """Project ONE seated PC's canonical mechanical state to a plain dict.

    Reads (confirmed from source): edge = single EdgePool (R3); xp/level/
    acquired_advancements from core (R4 — no tier/pending); location string
    + chassis current_room (R6); inventory aggregated digest (R7). Never
    raises on a partial model — missing attrs degrade to honest None/[]."""
    core = character.core
    edge = core.edge
    statuses = [
        {"text": getattr(s, "text", ""), "severity": getattr(s, "severity", "")}
        for s in (getattr(core, "statuses", None) or [])
    ]
    raw_items = getattr(getattr(core, "inventory", None), "items", None) or []
    return {
        "player_id": player_id,
        "character_name": character_name,
        "seat": seat,
        "round": round_number,
        "interaction": round_number,
        "location": location,
        "chassis_room": getattr(character, "current_room", None),
        "edge": {
            "current": getattr(edge, "current", None),
            "max": getattr(edge, "max", None),
            "base_max": getattr(edge, "base_max", None),
        },
        "down": bool(character.is_broken()),
        "statuses": statuses,
        "inventory": inventory_digest(raw_items),
        "inv_hash": inv_hash(raw_items),
        "gold": getattr(getattr(core, "inventory", None), "gold", 0),
        "xp": getattr(core, "xp", 0),
        "level": getattr(core, "level", 1),
        "acquired_advancements": list(
            getattr(core, "acquired_advancements", None) or []
        ),
        "ability_count": len(getattr(character, "abilities", None) or []),
    }


def build_trope_census(snapshot, round_number: int) -> dict:
    """Project SESSION-level trope state once per round (R5: tropes have
    no PC key; tension is not in the save and is excluded)."""
    tropes = []
    for t in getattr(snapshot, "active_tropes", None) or []:
        tropes.append(
            {
                "id": getattr(t, "id", ""),
                "status": getattr(t, "status", "dormant"),
                "progress": getattr(t, "progress", 0.0),
                "beats_fired": getattr(t, "beats_fired", 0),
                "last_fired_turn": getattr(t, "last_fired_turn", None),
            }
        )
    return {
        "round": round_number,
        "interaction": round_number,
        "active_tropes": tropes,
        "turns_since_meaningful": getattr(
            snapshot, "turns_since_meaningful", None
        ),
        "total_beats_fired": getattr(snapshot, "total_beats_fired", None),
    }
