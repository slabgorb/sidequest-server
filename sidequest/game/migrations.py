"""Read-old-write-new migration hook for ``GameSnapshot`` JSON.

Runs in ``SqliteStore.load`` BEFORE pydantic validation. Each migration
sub-function takes a snapshot dict, mutates a copy, and returns the
canonical shape. ``migrate_legacy_snapshot`` is the orchestrator — it
records which sub-functions actually rewrote anything and emits a single
``snapshot.canonicalize`` OTEL span with per-field attributes.

The architect's promise (per design 2026-05-04-snapshot-split-brain-cleanup):
this module is the ONLY place backward-compat shims live. When a save
predates a schema change, the shim lives here, not buried in pydantic
validators across the snapshot models. The lie-detector signal is one
span per load; the GM panel can audit which legacy shapes are still in
the wild.
"""

from __future__ import annotations

import copy
from typing import Any

from sidequest.telemetry.spans import SPAN_SNAPSHOT_CANONICALIZE, Span


def _migrate_s1_world_confrontations(out: dict[str, Any]) -> dict[str, Any] | None:
    """S1 — merge ``world_confrontations`` into ``magic_state.confrontations``.

    Dedupe by ``id``; existing ``magic_state.confrontations`` entries win
    on collision (magic_state is the canonical home — see design spec).
    Drops the legacy ``world_confrontations`` field after merge.

    Returns a dict of OTEL attributes when migration occurred, else None.
    """
    if "world_confrontations" not in out:
        return None

    legacy = out.pop("world_confrontations") or []

    if not legacy:
        return {
            "s1_world_confrontations_merged": 0,
            "s1_world_confrontations_dropped_no_target": 0,
        }

    magic_state = out.get("magic_state")
    if not isinstance(magic_state, dict):
        # No magic_state to migrate INTO — drop the entries rather than
        # synthesize a magic config. CLAUDE.md "No Silent Fallbacks": we
        # do not invent canonical state from absent inputs.
        return {
            "s1_world_confrontations_merged": 0,
            "s1_world_confrontations_dropped_no_target": len(legacy),
        }

    existing = magic_state.setdefault("confrontations", [])
    existing_ids = {c.get("id") for c in existing if isinstance(c, dict)}
    merged_count = 0
    for entry in legacy:
        if not isinstance(entry, dict):
            continue
        if entry.get("id") in existing_ids:
            continue  # collision — magic_state's entry wins
        existing.append(entry)
        existing_ids.add(entry.get("id"))
        merged_count += 1

    return {
        "s1_world_confrontations_merged": merged_count,
        "s1_world_confrontations_dropped_no_target": 0,
    }


def migrate_legacy_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a legacy snapshot dict into the canonical shape.

    Pure-ish: returns a new dict; does not mutate the input. Emits a
    ``snapshot.canonicalize`` OTEL span only when at least one
    sub-function rewrote a field — silent on canonical input.
    """
    out = copy.deepcopy(data)
    attributes: dict[str, Any] = {}

    # Migration sub-functions. Each returns either None (no-op) or a dict
    # of OTEL attributes to merge into the canonicalize span.
    for sub in (_migrate_s1_world_confrontations,):
        attrs = sub(out)
        if attrs is not None:
            attributes.update(attrs)

    if attributes:
        with Span.open(SPAN_SNAPSHOT_CANONICALIZE, attributes):
            pass

    return out
