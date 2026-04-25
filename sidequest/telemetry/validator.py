"""Layer-3 narrative validator — consumes TurnRecord, emits typed events.

Lifecycle: started by FastAPI's startup event (wired in Task 20), drained
on shutdown. A single asyncio.Task processes one TurnRecord at a time;
the queue is bounded and oldest-record-drops on QueueFull (faithful to
ADR-031's "lossy by design" intent).

The validator never raises into the dispatch hot path. Each check is
wrapped in try/except — a check exception fires a validation_warning
with severity=error rather than crashing the task.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable

from sidequest.telemetry.turn_record import TurnRecord
from sidequest.telemetry.watcher_hub import publish_event

logger = logging.getLogger(__name__)

CheckFn = Callable[[TurnRecord], Awaitable[None]]

# Capitalized two-word noun phrases — heuristic for "named entity in
# narration." Matches "Sir Reginald", "The Ironwood", "Lady Ashes" etc.
# False positives are fine — entity_check is a hint, not an oracle.
_NAMED_ENTITY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")


async def entity_check(record: TurnRecord) -> None:
    """Warn when narration names an NPC / region / item absent from the
    snapshot.

    Reads:
      - narration
      - snapshot_after.npc_registry (mapping name -> NpcRegistryEntry)
      - snapshot_after.discovered_regions (iterable of region names)
      - snapshot_after.inventory.items (iterable of item names)
    """
    snap = record.snapshot_after
    known_names: set[str] = set()
    npc_registry = getattr(snap, "npc_registry", None) or {}
    if isinstance(npc_registry, dict):
        known_names.update(npc_registry.keys())
    regions = getattr(snap, "discovered_regions", None) or ()
    known_names.update(str(r) for r in regions)
    inventory = getattr(snap, "inventory", None)
    if inventory is not None:
        items = getattr(inventory, "items", None) or ()
        for it in items:
            name = getattr(it, "name", None) or str(it)
            known_names.add(name)

    if not record.narration:
        return

    for match in _NAMED_ENTITY_RE.finditer(record.narration):
        candidate = match.group(1)
        if candidate not in known_names:
            publish_event(
                "validation_warning",
                {
                    "check": "entity",
                    "turn_id": record.turn_id,
                    "candidate": candidate,
                    "rationale": "narration names an entity not in snapshot",
                },
                component="validator",
                severity="warning",
            )
            # One warning per turn is sufficient; don't spam.
            return


_GRAB_VERBS = (
    "grab",
    "take",
    "pick up",
    "pocket",
    "stash",
    "loot",
    "snatch",
    "scoop",
    "lift",
    "claim",
)


async def inventory_check(record: TurnRecord) -> None:
    """Cross-check narration against inventory deltas."""
    narration = (record.narration or "").lower()
    delta = record.delta
    inv_changes = getattr(delta, "inventory_changes", None) or []
    has_inventory_patch = bool(inv_changes) or any(
        any("inventory" in f for f in p.fields_changed)
        for p in record.patches_applied
    )

    grabbed_in_narration = any(v in narration for v in _GRAB_VERBS)

    if grabbed_in_narration and not has_inventory_patch:
        publish_event(
            "validation_warning",
            {
                "check": "inventory",
                "turn_id": record.turn_id,
                "rationale": "narration describes a grab but no inventory patch",
            },
            component="validator",
            severity="warning",
        )

    for change in inv_changes:
        item = (
            change.get("item")
            if isinstance(change, dict)
            else getattr(change, "item", None)
        )
        if not item:
            continue
        if str(item).lower() not in narration:
            publish_event(
                "validation_warning",
                {
                    "check": "inventory",
                    "turn_id": record.turn_id,
                    "item": item,
                    "rationale": "patch added item but narration is silent",
                },
                component="validator",
                severity="warning",
            )


class Validator:
    """Single-consumer narrative validator pipeline."""

    def __init__(self, queue_maxsize: int = 32) -> None:
        self._queue: asyncio.Queue[TurnRecord] = asyncio.Queue(
            maxsize=queue_maxsize
        )
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._checks: list[CheckFn] = []
        # Health counters
        self.dropped_records: int = 0
        self._check_durations_ms: deque[tuple[str, float]] = deque(maxlen=200)
        self.register_check(entity_check)
        self.register_check(inventory_check)

    def register_check(self, fn: CheckFn) -> None:
        """Register a check coroutine. Called once per TurnRecord."""
        self._checks.append(fn)

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def submit(self, record: TurnRecord) -> None:
        """Enqueue a record. On QueueFull, drop the oldest record."""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self.dropped_records += 1
                publish_event(
                    "validation_warning",
                    {
                        "check": "validator.queue",
                        "reason": "queue_full",
                        "dropped_total": self.dropped_records,
                    },
                    component="validator",
                    severity="warning",
                )
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(record)
            except asyncio.QueueFull:
                self.dropped_records += 1

    async def start(self) -> None:
        if self.is_running():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(
            self._run(), name="sidequest.validator"
        )
        logger.info("validator.started")

    async def shutdown(self, grace_seconds: float = 2.0) -> None:
        self._stopping.set()
        if self._task is None:
            return
        # Drain remaining records up to the grace window.
        try:
            await asyncio.wait_for(
                self._queue.join(), timeout=grace_seconds
            )
        except TimeoutError:
            logger.warning(
                "validator.shutdown_grace_exceeded queued=%d",
                self._queue.qsize(),
            )
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("validator.stopped")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                record = await asyncio.wait_for(
                    self._queue.get(), timeout=0.5
                )
            except TimeoutError:
                continue
            try:
                await self._validate(record)
            finally:
                self._queue.task_done()

    async def _validate(self, record: TurnRecord) -> None:
        for check in self._checks:
            t0 = time.perf_counter()
            try:
                await check(record)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "validator.check_failed check=%s", check.__name__
                )
                publish_event(
                    "validation_warning",
                    {
                        "check": check.__name__,
                        "error": str(exc),
                        "turn_id": record.turn_id,
                    },
                    component="validator",
                    severity="error",
                )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._check_durations_ms.append(
                (check.__name__, elapsed_ms)
            )
