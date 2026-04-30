"""Genre-layer chassis pydantic loads sample YAML."""
from __future__ import annotations

import textwrap

import yaml

from sidequest.genre.models.chassis import ChassisClassesConfig

SAMPLE = textwrap.dedent("""
    version: "0.1.0"
    genre: space_opera
    classes:
      - id: voidborn_freighter
        display_name: "Voidborn Freighter"
        class: freighter
        provenance: voidborn_built
        scale_band: vehicular
        crew_model: flexible_roles
        embodiment_model: singular
        crew_awareness: surface
        psi_resonance:
          default: receptive
          amplifies: [void_singing]
        default_voice:
          default_register: dry_warm
          vocal_tics: ["theatrical sigh"]
          silence_register: approving_or_sulking_context_dependent
          name_forms_by_bond_tier:
            severed: "Pilot"
            hostile: "Pilot"
            strained: "Pilot"
            neutral: "Pilot"
            familiar: "Mr. {last_name}"
            trusted: "{first_name}"
            fused: "{nickname}"
        interior_rooms:
          - id: galley
            display_name: "Galley"
            bond_eligible_for: [the_tea_brew]
        crew_roles:
          - id: pilot
            operates_hardpoints: "*"
            bond_eligible: true
            default_seat: galley
""")


def test_chassis_classes_yaml_loads() -> None:
    cfg = ChassisClassesConfig.model_validate(yaml.safe_load(SAMPLE))
    assert cfg.genre == "space_opera"
    assert len(cfg.classes) == 1
    cls = cfg.classes[0]
    assert cls.id == "voidborn_freighter"
    assert cls.crew_model == "flexible_roles"
    assert cls.default_voice.name_forms_by_bond_tier["trusted"] == "{first_name}"
    assert cls.interior_rooms[0].id == "galley"


def test_unknown_crew_model_rejected() -> None:
    bad = yaml.safe_load(SAMPLE)
    bad["classes"][0]["crew_model"] = "nonsense"
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ChassisClassesConfig.model_validate(bad)
