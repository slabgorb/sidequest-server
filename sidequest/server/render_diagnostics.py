"""Post-session render-worker diagnostic writer (story 45-31, AC5).

At session end, write a JSON snapshot of the render worker's lifetime
to ``~/.sidequest/diagnostics/render-{room_slug}-{iso}.json`` so the
post-mortem of a Felix-style 13-minute silence can be diagnosed
without reproducing the crash.

JSON-on-disk is the v1 surface — story scope explicitly excludes
migrating into the events journal (a follow-up may absorb it).

The writer is also responsible for:
- emitting the ``daemon.session_diagnostic_written`` watcher event
  exactly once per write so the GM panel sees the substitution;
- refusing to escape the diagnostics directory on a malicious or
  buggy ``room_slug`` (path-traversal guard, no silent fallback).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Imported eagerly at module load so tests can monkeypatch
# ``render_diagnostics._watcher_publish`` to capture events without
# needing to reach through ``sidequest.telemetry.watcher_hub``.
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

logger = logging.getLogger(__name__)


_DIAGNOSTICS_SUBDIR = "diagnostics"
_REQUIRED_SNAPSHOT_FIELDS = (
    "heartbeat_history",
    "enqueue_count",
    "backpressure_warn_count",
    "unresponsive_windows",
    "last_successful_render_id",
    "last_successful_render_ts",
)


def _diagnostics_dir() -> Path:
    """Return ``~/.sidequest/diagnostics``. ``HOME`` is read live (not
    cached) so tests' ``monkeypatch.setenv("HOME", tmp_path)`` works.
    """
    return Path.home() / ".sidequest" / _DIAGNOSTICS_SUBDIR


def _sanitize_iso_for_filename(iso: str) -> str:
    """ISO timestamps contain ``:`` which is illegal on some
    filesystems and ugly on the rest. Replace with ``-`` so the file
    name greps cleanly in shell."""
    return iso.replace(":", "-")


def _validate_room_slug(room_slug: str) -> str:
    """Reject path-traversal slugs. Per CLAUDE.md "No Silent
    Fallbacks": fail loud on a slug that would escape the diagnostics
    dir, rather than silently sanitize it (which could mask a bug
    feeding the writer the wrong field)."""
    if not room_slug:
        raise ValueError("room_slug must be non-empty")
    if "/" in room_slug or "\\" in room_slug or ".." in room_slug:
        raise ValueError(
            f"room_slug {room_slug!r} contains path separators or "
            f"traversal sequences — refusing to write outside the "
            f"diagnostics dir"
        )
    return room_slug


def write_session_diagnostic(
    *,
    room_slug: str,
    session_end_iso: str,
    snapshot: dict[str, Any],
) -> Path:
    """Write a session-end render diagnostic JSON file.

    :param room_slug: Slug identifying the session room. Must not
        contain path separators or traversal sequences.
    :param session_end_iso: ISO-8601 timestamp of session end. Used
        verbatim in the filename (``:`` → ``-``).
    :param snapshot: Dict with the documented post-mortem fields
        (heartbeat_history, enqueue_count, backpressure_warn_count,
        unresponsive_windows, last_successful_render_id,
        last_successful_render_ts). Extra keys are preserved.
    :return: The path the diagnostic was written to.
    :raises ValueError: ``room_slug`` is empty or contains path
        traversal characters.
    """
    safe_slug = _validate_room_slug(room_slug)

    # Validate snapshot has every documented field (loud-on-missing —
    # a post-mortem with missing fields is silently impossible).
    for field in _REQUIRED_SNAPSHOT_FIELDS:
        if field not in snapshot:
            raise ValueError(
                f"snapshot missing required field {field!r}; "
                f"got keys={sorted(snapshot.keys())}"
            )

    target_dir = _diagnostics_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_iso = _sanitize_iso_for_filename(session_end_iso)
    path = target_dir / f"render-{safe_slug}-{safe_iso}.json"

    body = dict(snapshot)
    body["written_at_iso"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(body, indent=2, sort_keys=True))

    logger.info(
        "render.session_diagnostic_written path=%s heartbeat_count=%d",
        path,
        len(body.get("heartbeat_history") or []),
    )
    _watcher_publish(
        "state_transition",
        {
            "field": "render",
            "op": "session_diagnostic_written",
            "path": str(path),
            "room_slug": safe_slug,
            "heartbeat_count": len(body.get("heartbeat_history") or []),
            "unresponsive_window_count": len(
                body.get("unresponsive_windows") or []
            ),
            "enqueue_count": int(body.get("enqueue_count") or 0),
            "backpressure_warn_count": int(
                body.get("backpressure_warn_count") or 0
            ),
        },
        component="render",
    )
    return path


__all__ = ["write_session_diagnostic"]
