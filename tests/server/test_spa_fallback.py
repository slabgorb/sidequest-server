"""SPA fallback for the UI deep-link path.

Bug: tunnel deep-links 404 — when the server serves the built UI under
``SIDEQUEST_UI_DIST``, requests for client-side router paths
(``/play/<slug>``, ``/lobby``, etc.) must fall through to ``index.html``
so React Router can take over. Starlette's ``StaticFiles(html=True)``
only serves index.html for directory roots, not for arbitrary unmatched
paths — hence the 404 on ``/play/2026-05-03-coyote_star-mp``.

These tests pin the fix shape:
  1. Deep-link routes return ``index.html`` (200 + text/html).
  2. Real static assets in the dist tree are served verbatim.
  3. The catch-all does NOT steal API, WebSocket, or other registered routes.
  4. Without ``SIDEQUEST_UI_DIST`` set, the fallback is dormant (no
     regression for headless dev / test configurations).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.server.app import create_app

INDEX_HTML_BODY = (
    "<!doctype html><html><head><title>SideQuest</title></head>"
    '<body><div id="root"></div><script src="/assets/main.js"></script></body></html>'
)


@pytest.fixture
def ui_dist(tmp_path: Path) -> Path:
    """Build a minimal UI dist tree: index.html + assets/main.js."""
    dist = tmp_path / "ui-dist"
    dist.mkdir()
    (dist / "index.html").write_text(INDEX_HTML_BODY)
    assets = dist / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("console.log('hi');\n")
    (assets / "main.css").write_text("body{margin:0}\n")
    return dist


def _make_client(ui_dist: Path | None, monkeypatch) -> TestClient:
    if ui_dist is not None:
        monkeypatch.setenv("SIDEQUEST_UI_DIST", str(ui_dist))
    else:
        monkeypatch.delenv("SIDEQUEST_UI_DIST", raising=False)
    app = create_app()
    return TestClient(app)


def test_spa_fallback_returns_index_html_for_deep_link(ui_dist: Path, monkeypatch):
    """A client-side router path (``/play/<slug>``) must return index.html
    so the SPA can take over. This is the exact bug shape from the SM
    repro: ``https://sidequest.slabgorb.com/play/2026-05-03-coyote_star-mp``
    must NOT 404.
    """
    client = _make_client(ui_dist, monkeypatch)
    resp = client.get("/play/2026-05-03-coyote_star-mp")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in resp.text


def test_spa_fallback_serves_root_index(ui_dist: Path, monkeypatch):
    """Bare ``/`` still serves index.html."""
    client = _make_client(ui_dist, monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in resp.text


def test_spa_fallback_serves_real_static_asset(ui_dist: Path, monkeypatch):
    """Real files under the dist tree (e.g., Vite-bundled JS/CSS) must be
    served from disk, NOT rewritten to index.html.
    """
    client = _make_client(ui_dist, monkeypatch)
    resp = client.get("/assets/main.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text
    assert resp.headers["content-type"].startswith(("application/javascript", "text/javascript"))


def test_spa_fallback_does_not_steal_health(ui_dist: Path, monkeypatch):
    """``/health`` registers before the catch-all and must keep returning
    the health JSON.
    """
    client = _make_client(ui_dist, monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_spa_fallback_does_not_steal_api_routes(ui_dist: Path, monkeypatch):
    """REST routes under ``/api`` must register before the catch-all.
    ``/api/genres`` is part of the REST router and must return JSON
    (not the SPA shell).
    """
    client = _make_client(ui_dist, monkeypatch)
    resp = client.get("/api/genres")
    assert resp.status_code == 200
    # JSON dict, not HTML.
    assert resp.headers["content-type"].startswith("application/json")
    assert isinstance(resp.json(), dict)


def test_spa_fallback_dormant_without_env(monkeypatch):
    """Without ``SIDEQUEST_UI_DIST`` set, the catch-all does NOT register —
    deep links return 404 (current behavior), preserving the no-silent-
    fallback principle for unconfigured environments.
    """
    client = _make_client(None, monkeypatch)
    resp = client.get("/play/some-slug")
    assert resp.status_code == 404


def test_spa_fallback_dormant_when_env_points_at_missing_dir(monkeypatch, tmp_path):
    """If ``SIDEQUEST_UI_DIST`` points at a non-existent directory, the
    fallback must NOT register (loud-fail principle: misconfiguration
    surfaces as 404 + log warning, not as silent index-of-nothing).
    """
    monkeypatch.setenv("SIDEQUEST_UI_DIST", str(tmp_path / "does-not-exist"))
    app = create_app()
    client = TestClient(app)
    resp = client.get("/play/some-slug")
    assert resp.status_code == 404


def test_spa_fallback_does_not_serve_traversal_paths(ui_dist: Path, monkeypatch):
    """Path traversal attempts (``../etc/passwd``) must not escape the
    dist tree. The fallback should either 404 or return index.html — but
    NEVER read a file outside ``ui_dist``.
    """
    client = _make_client(ui_dist, monkeypatch)
    # The raw request goes through Starlette's URL normalization which
    # collapses ``..`` segments before the route handler runs, so the
    # path arrives as ``/etc/passwd``. Either it falls through to
    # index.html or it 404s — both are safe outcomes. The unsafe
    # outcome would be returning the host's /etc/passwd contents.
    resp = client.get("/etc/passwd")
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert '<div id="root"></div>' in resp.text
        assert "root:" not in resp.text  # not the unix passwd file


def test_spa_fallback_does_not_intercept_post(ui_dist: Path, monkeypatch):
    """The SPA fallback is GET-only. POST/PUT/DELETE on unknown paths
    should still 405/404 — non-GET requests for client-side routes don't
    make sense (the SPA only navigates via GET).
    """
    client = _make_client(ui_dist, monkeypatch)
    resp = client.post("/play/some-slug", json={})
    # 404 (no route) or 405 (method not allowed) — both fine; the
    # important thing is we don't return index.html as the body of a POST.
    assert resp.status_code in (404, 405)
