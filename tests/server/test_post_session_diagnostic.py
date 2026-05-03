"""RED tests — Story 45-31 — post-session diagnostic file (AC5).

After every session ends, the server writes a JSON diagnostic snapshot
of the render worker's lifetime to
``~/.sidequest/diagnostics/render-{room_slug}-{session_end_iso}.json``.

The snapshot is *post-mortem fuel*: a 13-minute Felix-style silence in
the next playtest must be diagnosable without reproducing the crash.
JSON-on-disk is the v1 surface (story scope explicitly excludes
migrating into the events journal).

These tests pin the contract:
- the file lands at the documented location with the documented name
  pattern;
- it carries ``heartbeat_history``, ``enqueue_count``,
  ``backpressure_warn_count``, ``unresponsive_windows``,
  ``last_successful_render_id``, ``last_successful_render_ts``;
- a ``daemon.session_diagnostic_written`` watcher event fires once
  on write so the GM panel sees the substitution;
- the writer fails loud (no silent fallback) when the diagnostics dir
  is unwritable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def test_diagnostic_module_is_importable() -> None:
    """The diagnostics writer lives in
    ``sidequest.server.render_diagnostics`` so the session-end teardown
    can import it without circular dependency on the WS handler module.

    Wiring guard — RED until Dev lands the module."""
    from sidequest.server import render_diagnostics  # noqa: F401


def test_diagnostic_writer_writes_to_documented_path(tmp_path, monkeypatch) -> None:
    """AC5: the diagnostic JSON file MUST land at
    ``~/.sidequest/diagnostics/render-{room_slug}-{iso}.json`` — the
    "known location" the post-mortem grep relies on. ``HOME`` is
    overridden so the test doesn't pollute the developer's real
    ``~/.sidequest/diagnostics``."""
    monkeypatch.setenv("HOME", str(tmp_path))

    from sidequest.server.render_diagnostics import write_session_diagnostic

    snapshot: dict[str, Any] = {
        "heartbeat_history": [
            {
                "queue": "image",
                "state": "ready",
                "ts_monotonic": 100.0,
                "ts_iso": "2026-05-03T14:43:00+00:00",
            }
        ],
        "enqueue_count": 5,
        "backpressure_warn_count": 1,
        "unresponsive_windows": [
            {
                "start_ts_iso": "2026-05-03T14:43:00+00:00",
                "end_ts_iso": "2026-05-03T14:56:00+00:00",
            }
        ],
        "last_successful_render_id": "abc123",
        "last_successful_render_ts": "2026-05-03T14:43:00+00:00",
    }

    path = write_session_diagnostic(
        room_slug="felix-room",
        session_end_iso="2026-05-03T14:56:00Z",
        snapshot=snapshot,
    )

    assert path.exists(), f"diagnostic file not written at {path}"
    assert path.parent == Path(tmp_path) / ".sidequest" / "diagnostics", (
        f"diagnostic file landed in unexpected dir {path.parent}"
    )
    # Filename pattern: render-{room_slug}-{iso}.json
    assert path.name.startswith("render-felix-room-"), (
        f"diagnostic file name does not match documented pattern: {path.name}"
    )
    assert path.suffix == ".json"


def test_diagnostic_payload_carries_required_fields(tmp_path, monkeypatch) -> None:
    """AC5: the JSON snapshot MUST include every field listed in the
    story context's "Post-session diagnostic" guardrail. Missing any
    one of them silently makes a future post-mortem impossible."""
    monkeypatch.setenv("HOME", str(tmp_path))

    from sidequest.server.render_diagnostics import write_session_diagnostic

    snapshot = {
        "heartbeat_history": [
            {
                "queue": "image",
                "state": "ready",
                "ts_monotonic": 100.0,
                "ts_iso": "2026-05-03T14:43:00+00:00",
            }
        ],
        "enqueue_count": 5,
        "backpressure_warn_count": 1,
        "unresponsive_windows": [
            {
                "start_ts_iso": "2026-05-03T14:43:00+00:00",
                "end_ts_iso": "2026-05-03T14:56:00+00:00",
            }
        ],
        "last_successful_render_id": "abc123",
        "last_successful_render_ts": "2026-05-03T14:43:00+00:00",
    }

    path = write_session_diagnostic(
        room_slug="felix-room",
        session_end_iso="2026-05-03T14:56:00Z",
        snapshot=snapshot,
    )

    body = json.loads(path.read_text())

    # Every field documented in the AC must be present and round-trip.
    required_fields = (
        "heartbeat_history",
        "enqueue_count",
        "backpressure_warn_count",
        "unresponsive_windows",
        "last_successful_render_id",
        "last_successful_render_ts",
    )
    for field in required_fields:
        assert field in body, (
            f"AC5: diagnostic snapshot missing required field '{field}'; "
            f"got keys={sorted(body.keys())}"
        )

    assert body["enqueue_count"] == 5
    assert body["backpressure_warn_count"] == 1
    assert len(body["unresponsive_windows"]) == 1
    assert body["last_successful_render_id"] == "abc123"


def test_diagnostic_writer_emits_watcher_event_once(
    tmp_path, monkeypatch
) -> None:
    """AC5: ``daemon.session_diagnostic_written`` watcher event must
    fire EXACTLY ONCE per write — the GM panel keys on this to confirm
    the substitution happened."""
    monkeypatch.setenv("HOME", str(tmp_path))

    from sidequest.server import render_diagnostics

    captured: list[tuple] = []

    def _capture(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append((event_type, fields, component, severity))

    monkeypatch.setattr(render_diagnostics, "_watcher_publish", _capture)

    render_diagnostics.write_session_diagnostic(
        room_slug="r1",
        session_end_iso="2026-05-03T14:56:00Z",
        snapshot={
            "heartbeat_history": [],
            "enqueue_count": 0,
            "backpressure_warn_count": 0,
            "unresponsive_windows": [],
            "last_successful_render_id": None,
            "last_successful_render_ts": None,
        },
    )

    written = [
        (et, f) for et, f, *_ in captured
        if et == "state_transition"
        and f.get("op") == "session_diagnostic_written"
    ]
    assert len(written) == 1, (
        f"expected exactly 1 daemon.session_diagnostic_written event, "
        f"got {len(written)}: {written}"
    )
    fields = written[0][1]
    # path attribute lets the GM panel deep-link.
    assert "path" in fields and fields["path"]
    assert fields.get("room_slug") == "r1"
    assert "heartbeat_count" in fields
    assert "unresponsive_window_count" in fields


def test_diagnostic_path_traversal_is_blocked(tmp_path, monkeypatch) -> None:
    """Defense: a malicious or buggy ``room_slug`` containing path
    separators MUST NOT escape ``~/.sidequest/diagnostics/``. Without
    the guard, a slug of ``../../etc`` would land on
    ``~/.sidequest/etc-...json`` (or worse). The writer must either
    reject the slug or sanitize it; either way the file MUST stay
    inside the diagnostics dir."""
    monkeypatch.setenv("HOME", str(tmp_path))

    from sidequest.server.render_diagnostics import write_session_diagnostic

    diagnostics_root = Path(tmp_path) / ".sidequest" / "diagnostics"

    # Either the call rejects with ValueError, or the resulting file is
    # confined to diagnostics_root.
    try:
        path = write_session_diagnostic(
            room_slug="../../escape",
            session_end_iso="2026-05-03T14:56:00Z",
            snapshot={
                "heartbeat_history": [],
                "enqueue_count": 0,
                "backpressure_warn_count": 0,
                "unresponsive_windows": [],
                "last_successful_render_id": None,
                "last_successful_render_ts": None,
            },
        )
    except ValueError:
        return  # Acceptable: rejected outright.

    # If it didn't reject, the resolved path MUST stay inside the
    # diagnostics dir. ``Path.is_relative_to`` is the cheap guard.
    resolved = path.resolve()
    assert resolved.is_relative_to(diagnostics_root.resolve()), (
        f"diagnostic file escaped diagnostics dir: {resolved} not under "
        f"{diagnostics_root}"
    )
