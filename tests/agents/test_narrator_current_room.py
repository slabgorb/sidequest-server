"""Wiring: narrator prompt surfaces current_room + state-patch instruction."""

from sidequest.agents.prompt_framework.core import PromptRegistry


def test_chassis_position_section_renders_each_pc_room():
    reg = PromptRegistry()
    reg.register_chassis_position_section(
        "narrator",
        {"Rux": "galley", "Orin": "cockpit"},
    )
    text = reg.compose("narrator")
    assert "Rux" in text
    assert "galley" in text
    assert "Orin" in text
    assert "cockpit" in text


def test_chassis_position_section_includes_state_patch_instruction():
    reg = PromptRegistry()
    reg.register_chassis_position_section(
        "narrator",
        {"Rux": "galley"},
    )
    text = reg.compose("narrator")
    # The narrator must be told to emit a state_patch when the prose
    # moves a character to a different room.
    assert "current_room" in text
    assert "state_patch" in text or "state patch" in text


def test_chassis_position_section_omits_when_empty():
    """Zero-byte-leak: empty dict produces no section (solo session, no chassis)."""
    reg = PromptRegistry()
    reg.register_chassis_position_section("narrator", {})
    text = reg.compose("narrator")
    assert "current_room" not in text
    assert "POSITION" not in text


def test_chassis_position_section_skips_unset_rooms():
    """Characters with current_room=None contribute nothing."""
    reg = PromptRegistry()
    # noqa: passing None explicitly — caller may include unset rooms
    reg.register_chassis_position_section(
        "narrator",
        {"Rux": "galley", "Orin": None},
    )
    text = reg.compose("narrator")
    assert "galley" in text
    # Orin has no room set; should not appear with an empty/None placeholder.
    assert "Orin is in" not in text or "in None" not in text
