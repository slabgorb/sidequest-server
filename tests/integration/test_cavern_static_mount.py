"""Wiring test: verify cavern_image_url resolves to a real served PNG.

Per CLAUDE.md 'every test suite needs a wiring test'.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.game.room_file_loader import load_room_payload
from sidequest.server.app import create_app


@pytest.fixture
def caverns_sunden_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    here = Path(__file__).resolve()
    repo = here.parents[3]
    content = repo / "sidequest-content"
    monkeypatch.setenv("SIDEQUEST_GENRE_PACKS", str(content / "genre_packs"))
    monkeypatch.setenv("SIDEQUEST_ASSET_BASE_URL", "")  # local mode
    return content / "genre_packs" / "caverns_and_claudes" / "worlds" / "caverns_sunden"


def test_cavern_image_url_serves_png_bytes(caverns_sunden_dir):
    payload = load_room_payload(caverns_sunden_dir, "mouth")
    app = create_app(
        genre_pack_search_paths=[caverns_sunden_dir.parent.parent.parent],
    )
    client = TestClient(app)
    # In local mode, cavern_image_url is /genre/...
    assert payload.cavern_image_url.startswith("/genre/")
    response = client.get(payload.cavern_image_url)
    assert response.status_code == 200
    assert response.headers["content-type"] in ("image/png", "image/x-png")
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
