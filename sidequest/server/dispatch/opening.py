"""Opening directive renderer + post-chargen resolution.

Replaces the old opening_hook.py. The unified Opening schema
(narrative.Opening) is composed against the world's chassis
instances, authored NPCs, magic register, and PC chargen choices
to produce a structured directive injected into the narrator's
Early zone for turn 1 only.

See docs/superpowers/specs/2026-05-01-canned-openings-design.md §2 + §6.
"""

from __future__ import annotations

import logging
import random

from sidequest.genre.models.authored_npc import AuthoredNpc
from sidequest.genre.models.chassis import BondTier
from sidequest.genre.models.narrative import (
    MagicMicrobleed,
    Opening,
    PerPcBeat,
)
from sidequest.genre.models.rigs_world import ChassisInstanceConfig

logger = logging.getLogger(__name__)


def _resolve_name_form(
    name_forms: dict[BondTier, str],
    tier: BondTier,
    *,
    first_name: str,
    last_name: str,
    nickname: str,
) -> str:
    template = name_forms.get(tier, "")
    return (
        template
        .replace("{first_name}", first_name)
        .replace("{last_name}", last_name)
        .replace("{nickname}", nickname or first_name)
    )


def _disposition_attitude(disposition: int) -> str:
    """Mirrors Npc.attitude() — three-tier ADR-020 mapping."""
    if disposition > 10:
        return "friendly"
    if disposition < -10:
        return "hostile"
    return "neutral"


def _render_directive_chassis(
    *,
    opening: Opening,
    chassis: ChassisInstanceConfig,
    authored_crew: list[AuthoredNpc],
    magic_register: str,
    bond_tier_for_pc: BondTier,
    per_pc_beat: PerPcBeat | None,
    pc_first_name: str,
    pc_last_name: str,
    pc_nickname: str,
) -> str:
    """Render a chassis-anchored opening directive.

    `authored_crew` is the resolved list of AuthoredNpc objects whose
    ids match `chassis.crew_npcs` (caller does the lookup). Order
    matches the chassis declaration.
    """
    parts: list[str] = ["=== OPENING SCENARIO ==="]
    parts.append(f"Mode: {opening.triggers.mode}")
    if opening.name:
        parts.append(f"Title: {opening.name}")

    interior_room_label = chassis.interior_rooms[0]
    if opening.setting.interior_room and opening.setting.interior_room in chassis.interior_rooms:
        interior_room_label = opening.setting.interior_room
    parts.append(f"Setting: aboard the {chassis.name}, {interior_room_label}")
    if opening.setting.situation:
        parts.append(f"Situation: {opening.setting.situation}")

    parts.append("")
    parts.append("ESTABLISHING NARRATION (play this scene):")
    parts.append(opening.establishing_narration)

    if chassis.voice is not None:
        parts.append("")
        parts.append(f"CHASSIS VOICE (the {chassis.name} speaks):")
        name_form = _resolve_name_form(
            chassis.voice.name_forms_by_bond_tier,
            bond_tier_for_pc,
            first_name=pc_first_name,
            last_name=pc_last_name,
            nickname=pc_nickname,
        )
        parts.append(f"- Name-form for this PC at bond_tier {bond_tier_for_pc}: \"{name_form}\"")
        parts.append(f"- Default register: {chassis.voice.default_register}")
        if chassis.voice.vocal_tics:
            parts.append(f"- Vocal tics: {', '.join(chassis.voice.vocal_tics)}")
        if chassis.voice.silence_register:
            parts.append(f"- Silence register: {chassis.voice.silence_register}")
        if opening.rig_voice_seeds:
            for seed in opening.rig_voice_seeds:
                ctx = str(seed.get("context", "")).strip()
                line = str(seed.get("line", "")).strip()
                if ctx and line:
                    parts.append(f"- {ctx}: {line}")
                elif line:
                    parts.append(f"- {line}")

    if magic_register:
        parts.append("")
        parts.append("MAGIC REGISTER:")
        parts.append(magic_register)

    if opening.magic_microbleed is not None:
        parts.append("")
        parts.append("MICROBLEED (one quiet uncanny detail to weave in once):")
        parts.append(opening.magic_microbleed.detail)
        if opening.magic_microbleed.cost_bar:
            parts.append(
                f"- Tick {opening.magic_microbleed.cost_bar} by 0.05 via narration."
            )

    if authored_crew:
        parts.append("")
        parts.append("PRE-LOADED NPCS PRESENT (already in registry — do NOT auto-register):")
        for npc in authored_crew:
            attitude = _disposition_attitude(npc.initial_disposition)
            line = f"- {npc.name} ({npc.role}): {npc.appearance}, disposition: {attitude}"
            parts.append(line)
            if npc.history_seeds:
                first_seed = npc.history_seeds[0]
                parts.append(f"  History: {first_seed}")

    if per_pc_beat is not None:
        parts.append("")
        parts.append("PER-PC BEAT (textural moment for this PC's chargen):")
        parts.append(per_pc_beat.beat)

    if opening.tone.register or opening.tone.stakes or opening.tone.avoid_at_all_costs:
        parts.append("")
        parts.append("TONE:")
        if opening.tone.register:
            parts.append(f"- Register: {opening.tone.register}")
        if opening.tone.stakes:
            parts.append(f"- Stakes: {opening.tone.stakes}")
        if opening.tone.sensory_layers:
            parts.append(f"- Sensory layers: {opening.tone.sensory_layers}")
        if opening.tone.avoid_at_all_costs:
            parts.append("- AVOID: " + "; ".join(opening.tone.avoid_at_all_costs))

    if opening.soft_hook.narration:
        parts.append("")
        parts.append("SOFT HOOK (only when conversation lulls; otherwise wait turn 2 or 3):")
        parts.append(opening.soft_hook.narration)
        if opening.soft_hook.timing:
            parts.append(f"- Timing: {opening.soft_hook.timing}")
        for k, v in opening.soft_hook.escalation_path.items():
            parts.append(f"- Escalation/{k}: {v}")

    if opening.party_framing is not None:
        parts.append("")
        parts.append("PARTY FRAMING:")
        if opening.party_framing.already_a_crew:
            parts.append("- The PCs are already a crew. Do not re-introduce them.")
        parts.append(f"- Default bond tier: {opening.party_framing.bond_tier_default}")
        for seed in opening.party_framing.shared_history_seeds:
            parts.append(f"  • {seed}")
        if opening.party_framing.narrator_guidance:
            parts.append(f"- {opening.party_framing.narrator_guidance}")

    if opening.first_turn_invitation:
        parts.append("")
        parts.append("FIRST TURN INVITATION (close the scene on this — NO closing question):")
        parts.append(opening.first_turn_invitation)

    parts.append("")
    parts.append("=== END OPENING ===")
    return "\n".join(parts)
