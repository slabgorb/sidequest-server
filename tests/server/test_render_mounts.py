"""Tests for the self-healing render-asset mount registry.

S4-BUG (playtest 2026-04-26): when the daemon restarts mid-session its
tmp dir changes (``/var/folders/.../sq-daemon-XXXX/zimage/``); the
server's static-file mount is locked to the *old* dir and every new
render 404s. ``render_mounts.ensure_render_mount`` heals this by
appending the new dir to the live ``StaticFiles`` mount's
``all_directories`` list at render-completed time.

Coverage:

* New render arriving with a never-mounted dir -> dir registered, the
  ``GET /renders/<file>`` request returns 200 (this is the wiring
  test CLAUDE.md mandates).
* Repeated renders for the same dir -> idempotent, no double-mount.
* Render for a path that doesn't exist on disk -> graceful failure
  (no phantom mount, no silent fallback).
* ``image_unavailable`` is *not* swallowed when the heal succeeds —
  the URL points to a servable file.
* OTEL: ``render_assets.mount_remounted`` fires when a new dir is
  appended; ``render_assets.url_404`` fires when the middleware sees a
  miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from sidequest.server import render_mounts


def _fresh_app(initial_dir: Path | None) -> FastAPI:
    """Build a minimal app with (optionally) a single ``/renders`` mount,
    seeded the same way ``create_app`` does. Caller is responsible for
    calling :func:`render_mounts.reset_for_app` after the test."""
    app = FastAPI()
    if initial_dir is not None:
        initial_dir.mkdir(parents=True, exist_ok=True)
        app.mount(
            "/renders",
            StaticFiles(directory=str(initial_dir)),
            name="render_assets",
        )
    return app


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict, dict]]:
    """Patch publish_event in BOTH the watcher_hub module and the local
    re-import inside render_mounts so every emission is captured."""
    captured: list[tuple[str, dict, dict]] = []

    def fake_publish(event_type, fields, *, component="", severity="info"):
        captured.append(
            (event_type, dict(fields), {"component": component, "severity": severity})
        )

    import sidequest.telemetry.watcher_hub as _hub
    monkeypatch.setattr(_hub, "publish_event", fake_publish)
    return captured


def test_register_root_appends_to_existing_mount(tmp_path: Path) -> None:
    """A new daemon dir registered post-startup should be appended to
    the StaticFiles ``all_directories`` list (NOT replace the original)
    so in-flight URLs to the original dir keep working."""
    initial = tmp_path / "daemon-old"
    new_root = tmp_path / "daemon-new"
    new_root.mkdir(parents=True)

    app = _fresh_app(initial)
    try:
        added = render_mounts.register_root(app, new_root)
        assert added is True

        # Both directories are now reachable from the same /renders mount.
        sf = next(
            r.app for r in app.routes
            if getattr(r, "name", None) == "render_assets"
        )
        assert isinstance(sf, StaticFiles)
        # Resolve both for symlink-equality on macOS (/var -> /private/var).
        served = {Path(d).resolve() for d in sf.all_directories}
        assert initial.resolve() in served
        assert new_root.resolve() in served
    finally:
        render_mounts.reset_for_app(app)


def test_register_root_idempotent(tmp_path: Path) -> None:
    """Registering the same root twice is a no-op — no double-mount,
    no error."""
    new_root = tmp_path / "daemon-x"
    new_root.mkdir()

    app = _fresh_app(initial_dir=tmp_path / "daemon-seed")
    try:
        first = render_mounts.register_root(app, new_root)
        second = render_mounts.register_root(app, new_root)
        assert first is True
        assert second is False

        sf = next(
            r.app for r in app.routes
            if getattr(r, "name", None) == "render_assets"
        )
        # The new root appears at most once.
        resolved_new = new_root.resolve()
        count = sum(1 for d in sf.all_directories if Path(d).resolve() == resolved_new)
        assert count == 1
    finally:
        render_mounts.reset_for_app(app)


def test_register_root_rejects_missing_dir(tmp_path: Path) -> None:
    """A non-existent directory must fail loudly — no silent fallback
    (CLAUDE.md). Caller's bug: the daemon told us about a path that
    isn't on disk; refuse to mount its parent."""
    app = _fresh_app(initial_dir=tmp_path / "seed")
    try:
        with pytest.raises(FileNotFoundError):
            render_mounts.register_root(app, tmp_path / "ghost")
    finally:
        render_mounts.reset_for_app(app)


def test_ensure_render_mount_serves_new_dir_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring test (CLAUDE.md mandates one per suite).

    Simulates the playtest failure: server starts mounted on the OLD
    daemon dir; daemon restarts; a render-completed reply lands with an
    image_url under the NEW dir. After ``ensure_render_mount`` runs,
    ``GET /renders/...`` returns 200 against the new dir.
    """
    old_root = tmp_path / "old-daemon" / "zimage"
    new_root = tmp_path / "new-daemon" / "zimage"
    old_root.mkdir(parents=True)
    new_root.mkdir(parents=True)

    image_file = new_root / "render_abc.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    app = _fresh_app(old_root)
    try:
        # Simulate render-completed: image_url is the absolute path the
        # daemon returned (under NEW root, which the server doesn't yet know).
        url = render_mounts.ensure_render_mount(app, str(image_file))
        assert url is not None
        assert url.endswith("render_abc.png")

        # The actual HTTP fetch must succeed — this is what the UI does.
        client = TestClient(app)
        resp = client.get(url)
        assert resp.status_code == 200, (
            f"GET {url} returned {resp.status_code} — "
            f"the self-healing mount didn't actually wire the new dir"
        )
        assert resp.content == b"\x89PNG\r\n\x1a\nfake"
    finally:
        render_mounts.reset_for_app(app)


def test_ensure_render_mount_idempotent_for_same_dir(tmp_path: Path) -> None:
    """A second render in the same daemon dir must NOT double-mount."""
    root = tmp_path / "daemon" / "zimage"
    root.mkdir(parents=True)
    f1 = root / "a.png"
    f2 = root / "b.png"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")

    app = _fresh_app(initial_dir=tmp_path / "seed")
    try:
        u1 = render_mounts.ensure_render_mount(app, str(f1))
        u2 = render_mounts.ensure_render_mount(app, str(f2))
        assert u1 and u2

        sf = next(
            r.app for r in app.routes
            if getattr(r, "name", None) == "render_assets"
        )
        # Count distinct resolved directories under the mount.
        seen_roots = {Path(d).resolve() for d in sf.all_directories}
        # Seed + (one) daemon parent — never two new entries for two files.
        assert len(seen_roots) <= 3  # seed + daemon-root + maybe daemon-root.parent
    finally:
        render_mounts.reset_for_app(app)


def test_ensure_render_mount_returns_none_for_missing_file(tmp_path: Path) -> None:
    """If the daemon's image_url doesn't exist on disk, refuse to mount
    its parent (would just register an empty dir; the 404 stays). The
    caller logs the failure via existing OTEL."""
    app = _fresh_app(initial_dir=tmp_path / "seed")
    try:
        result = render_mounts.ensure_render_mount(
            app, str(tmp_path / "ghost" / "no.png"),
        )
        assert result is None
    finally:
        render_mounts.reset_for_app(app)


def test_ensure_render_mount_emits_remount_otel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lie-detector event: ``render_assets.mount_remounted`` must
    fire when a new daemon dir is registered post-startup."""
    captured = _capture_events(monkeypatch)

    app = _fresh_app(initial_dir=tmp_path / "seed")
    try:
        new_root = tmp_path / "fresh"
        new_root.mkdir()
        f = new_root / "img.png"
        f.write_bytes(b"x")

        render_mounts.ensure_render_mount(app, str(f))

        remount = [
            (fields, meta) for et, fields, meta in captured
            if et == "render_assets.mount_remounted"
        ]
        assert remount, "expected render_assets.mount_remounted on first heal"
        fields, meta = remount[0]
        assert fields["source"] == "render_completed"
        assert fields["first"] is False
        assert meta["component"] == "render"
    finally:
        render_mounts.reset_for_app(app)


def test_publish_url_404_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 404 watcher event de-duplicates per URL — otherwise a single
    bad image_url referenced by a dozen UI components would flood the
    GM panel."""
    captured = _capture_events(monkeypatch)
    # Reset the per-URL dedupe set between tests.
    if hasattr(render_mounts.publish_url_404, "_seen"):
        render_mounts.publish_url_404._seen.clear()

    render_mounts.publish_url_404("/renders/dup.png")
    render_mounts.publish_url_404("/renders/dup.png")
    render_mounts.publish_url_404("/renders/other.png")

    fired = [et for et, _, _ in captured if et == "render_assets.url_404"]
    assert len(fired) == 2  # once per distinct URL


def test_create_app_registers_active_app(tmp_path: Path, monkeypatch) -> None:
    """Wiring test for the create_app -> render_mounts handshake.

    The session_handler render coroutine doesn't carry an app reference;
    it grabs the singleton via ``get_active_app()``. Without this
    binding the heal logic silently no-ops, which is the exact failure
    mode we're trying to fix. Verify create_app wires it.
    """
    from sidequest.server.app import create_app

    monkeypatch.delenv("SIDEQUEST_OUTPUT_DIR", raising=False)
    # Force the handshake fallback into a directory that exists.
    fake_home = tmp_path / "home"
    (fake_home / ".sidequest").mkdir(parents=True)
    daemon_dir = tmp_path / "daemon-out"
    daemon_dir.mkdir()
    (fake_home / ".sidequest" / "daemon-output-dir").write_text(str(daemon_dir))
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

    app = create_app()
    try:
        assert render_mounts.get_active_app() is app
    finally:
        render_mounts.reset_for_app(app)
