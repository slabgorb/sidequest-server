"""End-to-end: load_genre_pack + init_chassis_registry → Kestrel materialized."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SPACE_OPERA = REPO_ROOT / "sidequest-content" / "genre_packs" / "space_opera"


@pytest.mark.integration
def test_kestrel_materializes_with_voice_and_bond() -> None:
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")

    from sidequest.game.chassis import init_chassis_registry
    from sidequest.game.session import GameSnapshot
    from sidequest.genre.loader import load_genre_pack

    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_reach",
        location="Unknown",
    )
    init_chassis_registry(snap, pack)

    assert "kestrel" in snap.chassis_registry
    kestrel = snap.chassis_registry["kestrel"]
    assert kestrel.bond_ledger[0].bond_tier_chassis == "trusted"
    assert kestrel.voice is not None
    assert kestrel.voice.name_forms_by_bond_tier["trusted"] == "{first_name}"

    # Projection visible in npc_registry
    names = {entry.name for entry in snap.npc_registry}
    assert "Kestrel" in names
