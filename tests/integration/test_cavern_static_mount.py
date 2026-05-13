"""Wiring test: verify cavern_image_url resolves to a real served PNG.

Per CLAUDE.md 'every test suite needs a wiring test'.

The .cavern.png is rendered locally and uploaded to R2 (canonical home —
see sidequest-content commit ff99fb4 stripping the 46 PNGs from git).
This test exercises the URL → mount → static-file pipeline; the PNG
itself is generated in-fixture so the wiring check is hermetic and does
not depend on a prior cavern_renderer run.
"""

import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.game.room_file_loader import load_room_payload
from sidequest.server.app import create_app

# A 1x1 transparent PNG — minimum bytes that pass content-type + magic
# sniffing. Keeps the fixture hermetic; real renders live in R2.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _make_min_png() -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    raw = b"\x00\x00\x00\x00\x00"
    idat = _chunk(b"IDAT", zlib.compress(raw))
    iend = _chunk(b"IEND", b"")
    return _PNG_MAGIC + ihdr + idat + iend


@pytest.fixture
def caverns_sunden_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    here = Path(__file__).resolve()
    repo = here.parents[3]
    content = repo / "sidequest-content"
    monkeypatch.setenv("SIDEQUEST_GENRE_PACKS", str(content / "genre_packs"))
    monkeypatch.setenv("SIDEQUEST_ASSET_BASE_URL", "")  # local mode
    return content / "genre_packs" / "caverns_and_claudes" / "worlds" / "caverns_sunden"


def test_cavern_image_url_serves_png_bytes(caverns_sunden_dir: Path, tmp_path: Path) -> None:
    # Drop a hermetic PNG at the expected on-disk location. R2 is canonical
    # for real deployments; the wiring test only needs SOME PNG so the
    # static mount can serve it. Restored after the test via tmp_path
    # ownership of the parent isn't possible (real content dir), so we
    # write+cleanup explicitly.
    png_path = caverns_sunden_dir / "rooms" / "mouth.cavern.png"
    created = not png_path.exists()
    if created:
        png_path.write_bytes(_make_min_png())
    try:
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
        assert response.content[:8] == _PNG_MAGIC
    finally:
        if created and png_path.exists():
            png_path.unlink()
