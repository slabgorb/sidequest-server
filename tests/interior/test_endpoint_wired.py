"""Wiring test: GET /api/chassis/{id}/interior reaches the renderer.

Uses the live sidequest-content space_opera pack so this fails loudly
if the chassis YAML drifts in a way that silently breaks the endpoint.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.server.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENT_GENRE_PACKS = REPO_ROOT.parent / "sidequest-content" / "genre_packs"


def _client_with_content():
    if not CONTENT_GENRE_PACKS.exists():
        pytest.skip("sidequest-content genre_packs not present")
    app = create_app(genre_pack_search_paths=[CONTENT_GENRE_PACKS])
    return TestClient(app)


def test_interior_endpoint_returns_svg_for_kestrel():
    client = _client_with_content()
    resp = client.get("/api/chassis/kestrel/interior")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/svg+xml")
    body = resp.text
    assert "Kestrel" in body
    assert 'data-room="cockpit"' in body
    assert 'data-station="helm"' in body
    # Stub crew NPCs land on the map at their default rooms.
    assert 'data-actor="kestrel_captain"' in body


def test_interior_endpoint_404_on_unknown_chassis():
    client = _client_with_content()
    resp = client.get("/api/chassis/nonexistent_ship/interior")
    assert resp.status_code == 404
