"""World-layer rigs pydantic loads sample YAML."""
from __future__ import annotations

import textwrap

import yaml

from sidequest.genre.models.rigs_world import RigsWorldConfig

SAMPLE = textwrap.dedent("""
    version: "0.1.0"
    world: coyote_star
    genre: space_opera
    chassis_instances:
      - id: kestrel
        name: "Kestrel"
        class: voidborn_freighter
        OCEAN: { O: 0.6, C: 0.7, E: 0.4, A: 0.5, N: 0.5 }
        voice:
          default_register: dry_warm
          vocal_tics: ["dry as bonemeal"]
          name_forms_by_bond_tier:
            severed: "Pilot"
            hostile: "Pilot"
            strained: "Pilot"
            neutral: "Pilot"
            familiar: "Mr. {last_name}"
            trusted: "{first_name}"
            fused: "{nickname}"
        interior_rooms: [cockpit, galley]
        bond_seeds:
          - character_role: player_character
            bond_strength_character_to_chassis: 0.45
            bond_strength_chassis_to_character: 0.45
            bond_tier_character: trusted
            bond_tier_chassis: trusted
            history_seeds:
              - "muscle memory from three jumps' worth of patch kits"
""")


def test_rigs_yaml_loads() -> None:
    cfg = RigsWorldConfig.model_validate(yaml.safe_load(SAMPLE))
    assert cfg.world == "coyote_star"
    assert len(cfg.chassis_instances) == 1
    inst = cfg.chassis_instances[0]
    assert inst.id == "kestrel"
    assert inst.bond_seeds[0].bond_tier_chassis == "trusted"
    assert inst.OCEAN.O == 0.6


def test_unknown_bond_tier_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    bad = yaml.safe_load(SAMPLE)
    bad["chassis_instances"][0]["bond_seeds"][0]["bond_tier_chassis"] = "nonsense"
    with pytest.raises(ValidationError):
        RigsWorldConfig.model_validate(bad)
