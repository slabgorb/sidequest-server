"""Tests for prompt_framework/soul.py.

Port of sidequest-agents/src/prompt_framework/tests.rs — SOUL.md parser and
SoulData method test blocks.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from sidequest.agents.prompt_framework.soul import parse_soul_md

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_temp_soul(content: str) -> Path:
    """Write content to a temp file and return its path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
    return Path(f.name)


# =========================================================================
# SOUL.md parser tests
# =========================================================================


def test_parse_soul_md_extracts_principles_from_real_format():
    content = """\
# SOUL.md — SideQuest Agent Guidelines

Rules that govern how all AI agents interact with players.

**Agency.** The player controls their character — actions, thoughts, feelings.

**Living World.** NPCs act on their own goals — especially villains.

**Genre Truth.** Consequences follow the genre pack's tone and lethality.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert len(data.principles) == 3
    assert data.principles[0].name == "Agency"
    assert data.principles[1].name == "Living World"
    assert data.principles[2].name == "Genre Truth"


def test_parse_soul_md_extracts_body_text():
    content = """\
# SOUL.md

**Agency.** The player controls their character — actions, thoughts, feelings.

**Living World.** NPCs act on their own goals.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert "The player controls their character" in data.principles[0].text


def test_parse_soul_md_extracts_title():
    content = """\
# SOUL.md — SideQuest Agent Guidelines

Rules that govern how all AI agents interact with players.

**Agency.** The player controls their character.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert data.title == "SOUL.md — SideQuest Agent Guidelines"


def test_parse_soul_md_extracts_description():
    content = """\
# SOUL.md — SideQuest Agent Guidelines

Rules that govern how all AI agents interact with players.

**Agency.** The player controls their character.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert data.description == "Rules that govern how all AI agents interact with players."


def test_parse_soul_md_nonexistent_file_returns_empty():
    data = parse_soul_md(Path("/nonexistent/SOUL.md"))
    assert data.is_empty()
    assert data.title is None


def test_parse_soul_md_empty_file_returns_empty():
    path = write_temp_soul("")
    data = parse_soul_md(path)
    assert data.is_empty()


def test_parse_soul_md_file_without_bold_headers_returns_empty():
    path = write_temp_soul("Just some plain text without any bold headers.\n\nAnother paragraph.")
    data = parse_soul_md(path)
    assert data.is_empty()


def test_parse_soul_md_preserves_document_order():
    content = """\
# SOUL.md

**Zebra.** Last alphabetically but first in doc.

**Alpha.** First alphabetically but second in doc.

**Middle.** Middle of everything.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert data.principles[0].name == "Zebra"
    assert data.principles[1].name == "Alpha"
    assert data.principles[2].name == "Middle"


def test_parse_soul_md_handles_multiline_body():
    content = """\
# SOUL.md

**Diamonds and Coal.** Detail signals importance. Match narrative detail to narrative weight. Coal can become a diamond when the players choose to polish it.

**Next Principle.** Something else.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert data.principles[0].name == "Diamonds and Coal"
    assert "Detail signals importance" in data.principles[0].text
    assert "Coal can become a diamond" in data.principles[0].text


# =========================================================================
# SoulData method tests
# =========================================================================


def test_soul_data_len_returns_principle_count():
    content = """\
# SOUL.md

**One.** First.

**Two.** Second.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)
    assert data.len() == 2


def test_soul_data_is_empty_true_for_no_principles():
    path = write_temp_soul("Just text.")
    data = parse_soul_md(path)
    assert data.is_empty()


def test_soul_data_is_empty_false_when_principles_exist():
    content = "**One.** First.\n\n"
    path = write_temp_soul(content)
    data = parse_soul_md(path)
    assert not data.is_empty()


def test_soul_data_get_finds_by_name_case_insensitive():
    content = """\
# SOUL.md

**Agency.** The player controls.

**Living World.** NPCs act independently.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    agency = data.get("agency")
    assert agency is not None
    assert agency.name == "Agency"

    living = data.get("LIVING WORLD")
    assert living is not None


def test_soul_data_get_returns_none_for_missing():
    content = "**Agency.** The player controls.\n\n"
    path = write_temp_soul(content)
    data = parse_soul_md(path)
    assert data.get("nonexistent") is None


def test_soul_data_as_prompt_text_formats_as_important_blocks():
    content = """\
**Agency.** The player controls.

**Living World.** NPCs act.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)
    text = data.as_prompt_text()

    assert "<important>\nAgency: The player controls.\n</important>" in text
    assert "<important>\nLiving World: NPCs act.\n</important>" in text


def test_soul_data_as_prompt_text_empty_for_no_principles():
    path = write_temp_soul("Just text.")
    data = parse_soul_md(path)
    assert data.as_prompt_text() == ""


# =========================================================================
# agents tag filtering
# =========================================================================


def test_parse_soul_md_parses_agents_tag():
    content = """\
**Agency.** <agents>narrator,troper</agents> The player controls their character.

**Opsec.** <agents>none</agents> Internal design note only.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert data.principles[0].agents == ["narrator", "troper"]
    assert data.principles[1].agents == ["none"]


def test_parse_soul_md_defaults_agents_to_all():
    content = "**Agency.** The player controls their character.\n\n"
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert data.principles[0].agents == ["all"]


def test_soul_data_agents_tag_stripped_from_text():
    content = "**Agency.** <agents>narrator</agents> The player controls their character.\n\n"
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    assert "<agents>" not in data.principles[0].text
    assert "The player controls their character" in data.principles[0].text


def test_as_prompt_text_for_narrator_excludes_covered_principles():
    """Narrator should not receive Agency or Genre Truth (handled by Primacy guardrails)."""
    content = """\
**Agency.** The player controls.

**Genre Truth.** Follow the genre.

**Living World.** NPCs act independently.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    text = data.as_prompt_text_for("narrator")
    assert "Agency" not in text
    assert "Genre Truth" not in text
    assert "Living World" in text


def test_as_prompt_text_for_other_agent_gets_agency():
    """Non-narrator agents should receive Agency."""
    content = """\
**Agency.** The player controls.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    text = data.as_prompt_text_for("troper")
    assert "Agency" in text


def test_as_prompt_text_for_excludes_none_agents():
    content = """\
**Opsec.** <agents>none</agents> Internal design note only.

**Living World.** NPCs act.
"""
    path = write_temp_soul(content)
    data = parse_soul_md(path)

    text = data.as_prompt_text_for("narrator")
    assert "Opsec" not in text
    assert "Living World" in text
