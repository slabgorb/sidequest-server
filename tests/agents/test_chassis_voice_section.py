"""Narrator prompt — register_chassis_voice_section renders voice + name-form."""
from __future__ import annotations

from sidequest.agents.prompt_framework.core import PromptRegistry
from sidequest.game.chassis import BondLedgerEntry, ChassisInstance
from sidequest.genre.models.chassis import ChassisVoiceSpec

_AGENT = "narrator"


def _kestrel(strength: float = 0.45) -> ChassisInstance:
    return ChassisInstance(
        id="kestrel",
        name="Kestrel",
        class_id="voidborn_freighter",
        voice=ChassisVoiceSpec(
            default_register="dry_warm",
            vocal_tics=["theatrical sigh", "almost-but-not-quite a laugh"],
            silence_register="approving_or_sulking_context_dependent",
            name_forms_by_bond_tier={
                "severed": "Pilot",
                "hostile": "Pilot",
                "strained": "Pilot",
                "neutral": "Pilot",
                "familiar": "Mr. {last_name}",
                "trusted": "{first_name}",
                "fused": "{nickname}",
            },
        ),
        bond_ledger=[
            BondLedgerEntry(
                character_id="player_character",
                bond_strength_character_to_chassis=strength,
                bond_strength_chassis_to_character=strength,
                bond_tier_character="trusted",
                bond_tier_chassis="trusted",
            ),
        ],
    )


def _registry_with_section(chassis_registry, character_name: str) -> str:
    reg = PromptRegistry()
    reg.register_chassis_voice_section(_AGENT, chassis_registry, character_name)
    return reg.render_for(_AGENT)


def test_section_renders_kestrel_with_first_name_form() -> None:
    rendered = _registry_with_section({"kestrel": _kestrel()}, "Zee Jones")
    assert "Kestrel" in rendered
    assert "dry_warm" in rendered
    assert "theatrical sigh" in rendered
    assert "Zee" in rendered  # trusted-tier first-name form


def test_section_renders_last_name_form_at_familiar_tier() -> None:
    chassis = _kestrel()
    chassis.bond_ledger[0].bond_tier_chassis = "familiar"
    rendered = _registry_with_section({"kestrel": chassis}, "Zee Jones")
    assert "Mr. Jones" in rendered


def test_section_silent_when_chassis_has_no_voice() -> None:
    chassis = _kestrel()
    chassis.voice = None
    rendered = _registry_with_section({"kestrel": chassis}, "Zee Jones")
    # No voice means no chassis-as-speaker prose — section should not render
    # this chassis. Check that no chassis-voice header leaked into the prompt.
    assert "chassis voice" not in rendered.lower()


def test_section_silent_on_empty_registry() -> None:
    rendered = _registry_with_section({}, "Zee Jones")
    # Empty registry must produce ZERO bytes (zero-byte-leak discipline,
    # mirrors register_npc_roster_section).
    assert "chassis" not in rendered.lower()


def test_single_word_name_falls_back_to_first_only() -> None:
    rendered = _registry_with_section({"kestrel": _kestrel()}, "Zee")
    # Trusted tier renders {first_name} so single-word name still works.
    assert "Zee" in rendered


def test_no_bond_entry_renders_default_pilot_form() -> None:
    """Narrator should still see the chassis when bond hasn't been seeded yet."""
    chassis = _kestrel()
    chassis.bond_ledger = []  # no entries
    rendered = _registry_with_section({"kestrel": chassis}, "Zee Jones")
    assert "Kestrel" in rendered
    # voice resolver returns "Pilot" when no bond entry.
    assert "Pilot" in rendered
