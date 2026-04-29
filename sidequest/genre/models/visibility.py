"""Visibility baseline + overrides — YAML schema loaded per genre pack + world.

The decomposer reads the effective (baseline + overrides) model at session
init and uses it as the default VisibilityTag emission when no turn state
suggests otherwise. The YAML lives in sidequest-content, not in this repo.

Validation is strict (extra='forbid'). Unknown subsystem names or fidelity
levels raise at pack-load time, not at runtime — see CLAUDE.md "no silent
fallbacks".
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from sidequest.protocol.dispatch import PerceptionFidelity

Tone = Literal["broadcast_heavy", "balanced", "secret_heavy"]
AllScope = Literal["protagonists", "party_plus_guest_npcs"]
FidelityVerb = Literal["drop", "keep", "muffle"]

# Keep in sync with sidequest.agents.local_dm KNOWN_SUBSYSTEMS at Task-time.
# Kept here as a local constant rather than imported to break the cycle
# (local_dm imports from protocol, which would pull this in at runtime).
KNOWN_SUBSYSTEM_KEYS = frozenset(
    {
        "npc_agency",
        "confrontation_init",
        "stealth_roll_check",
        "lore_reveal",
        "dice_roll_private",
        "exploration",
        "distinctive_detail",
        "reflect_absence",
    }
)


class VisibilityBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: Tone
    default_visibility: dict[str, Literal["all", "actor_only", "audio_only_muffled"]]
    status_effect_fidelity: dict[str, dict[PerceptionFidelity, FidelityVerb]] = {}
    all_scope: AllScope = "protagonists"

    @model_validator(mode="after")
    def _known_subsystems(self) -> VisibilityBaseline:
        unknown = set(self.default_visibility) - KNOWN_SUBSYSTEM_KEYS
        if unknown:
            raise ValueError(
                f"default_visibility references unknown subsystem(s): {sorted(unknown)}. "
                f"Allowed: {sorted(KNOWN_SUBSYSTEM_KEYS)}"
            )
        return self

    @classmethod
    def model_validate_yaml(cls, text: str) -> VisibilityBaseline:
        raw = yaml.safe_load(text) or {}
        return cls.model_validate(raw)


class VisibilityOverrides(BaseModel):
    """Per-world deltas. Only fields that override baseline."""

    model_config = ConfigDict(extra="forbid")

    tone: Tone | None = None
    default_visibility: dict[str, Literal["all", "actor_only", "audio_only_muffled"]] = {}
    status_effect_fidelity: dict[str, dict[PerceptionFidelity, FidelityVerb]] = {}

    @classmethod
    def model_validate_yaml(cls, text: str) -> VisibilityOverrides:
        raw = yaml.safe_load(text) or {}
        return cls.model_validate(raw)


def load_baseline(path: Path) -> VisibilityBaseline:
    """Load and validate visibility_baseline.yaml. Raises on missing/invalid."""
    return VisibilityBaseline.model_validate_yaml(path.read_text())


def load_overrides(path: Path) -> VisibilityOverrides:
    """Load and validate visibility_overrides.yaml. Raises on missing/invalid."""
    return VisibilityOverrides.model_validate_yaml(path.read_text())


def effective_visibility(
    baseline: VisibilityBaseline,
    overrides: VisibilityOverrides | None,
) -> VisibilityBaseline:
    """Return a new baseline with overrides' non-empty fields applied."""
    if overrides is None:
        return baseline
    merged = baseline.model_dump()
    if overrides.tone is not None:
        merged["tone"] = overrides.tone
    if overrides.default_visibility:
        merged["default_visibility"] = {
            **merged["default_visibility"],
            **overrides.default_visibility,
        }
    if overrides.status_effect_fidelity:
        merged_fx = dict(merged.get("status_effect_fidelity", {}))
        for effect, mapping in overrides.status_effect_fidelity.items():
            merged_fx[effect] = {**merged_fx.get(effect, {}), **mapping}
        merged["status_effect_fidelity"] = merged_fx
    return VisibilityBaseline.model_validate(merged)
