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


@pytest.mark.integration
def test_kestrel_voice_section_renders_in_narrator_prompt() -> None:
    """End-of-Phase-A smoke test: world load → chassis_registry → voice section renders.

    Goes from raw genre-pack load all the way to a built narrator prompt
    that contains Kestrel's voice block with bond-tier-correct name-form.
    Pre-bonded at trusted (0.45) per rigs.yaml, so name-form is the
    {first_name} template = "Zee" for character_name="Zee Jones".
    """
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")

    from sidequest.agents.prompt_framework.core import PromptRegistry
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

    registry = PromptRegistry()
    registry.register_chassis_voice_section(
        "narrator", snap.chassis_registry, character_name="Zee Jones",
    )
    rendered = registry.render_for("narrator")

    # Voice section header rendered
    assert "CHASSIS VOICES" in rendered
    # Chassis name visible
    assert "Kestrel" in rendered
    # Default register from world-layer voice block
    assert "dry_warm" in rendered
    # Trusted-tier first-name form (chassis was pre-bonded at 0.45)
    assert "Zee" in rendered
    # World-layer vocal tic ("dry as bonemeal" was added at the world layer
    # — confirms the world voice block, not just the class default, reached
    # the prompt)
    assert "dry as bonemeal" in rendered
