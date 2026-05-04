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
    # No sub-functions registered yet — this is the scaffold-only step.

    if attributes:
        with Span.open(SPAN_SNAPSHOT_CANONICALIZE, attributes):
            pass

    return out
