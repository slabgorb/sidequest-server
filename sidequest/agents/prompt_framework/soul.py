"""SOUL.md parser — extracts guiding principles for agent prompt injection.

Parses bold-header paragraphs (**Name.** Body text) from SOUL.md into
SoulPrinciple objects. Port of sidequest-agents/src/prompt_framework/soul.rs.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

# SOUL principles that the narrator agent already covers via Primacy guardrails (story 23-10).
# These are excluded from the narrator's SOUL injection to prevent double-injection.
_NARRATOR_COVERED_PRINCIPLES: frozenset[str] = frozenset(["Agency", "Genre Truth"])


class SoulPrinciple(BaseModel):
    """A named guiding principle from SOUL.md."""

    model_config = {"frozen": True}

    name: str
    text: str
    agents: list[str]


class SoulData(BaseModel):
    """Parsed SOUL.md structure — all principles in document order."""

    model_config = {"frozen": True}

    principles: list[SoulPrinciple] = []
    title: str | None = None
    description: str | None = None

    def __len__(self) -> int:
        return len(self.principles)

    def len(self) -> int:
        """Returns the number of principles."""
        return len(self.principles)

    def is_empty(self) -> bool:
        """Returns true if there are no principles."""
        return len(self.principles) == 0

    def get(self, name: str) -> SoulPrinciple | None:
        """Look up a principle by name (case-insensitive)."""
        lower = name.lower()
        for p in self.principles:
            if p.name.lower() == lower:
                return p
        return None

    def as_prompt_text(self) -> str:
        """Format all principles as <important> XML blocks for prompt injection."""
        return "\n\n".join(
            f"<important>\n{p.name}: {p.text}\n</important>" for p in self.principles
        )

    def as_prompt_text_for(self, agent: str) -> str:
        """Format principles for a specific agent, filtering by <agents> tags.

        Includes principles tagged 'all' or containing the agent name.
        Excludes principles tagged 'none'.

        For the narrator agent, excludes principles that overlap with narrator-specific
        Primacy guardrails (story 23-10: Agency → narrator_agency, Genre Truth →
        narrator_consequences). The narrator versions are richer.
        """
        filtered = []
        for p in self.principles:
            agent_match = any(a == "all" or a == agent for a in p.agents)
            narrator_excluded = agent == "narrator" and p.name in _NARRATOR_COVERED_PRINCIPLES
            if agent_match and not narrator_excluded:
                filtered.append(p)
        return "\n\n".join(f"<important>\n{p.name}: {p.text}\n</important>" for p in filtered)


_PRINCIPLE_RE = re.compile(r"\*\*([^*]+?)\.\*\*\s*(.+)")
_AGENTS_RE = re.compile(r"<agents>([^<]+)</agents>\s*")


def parse_soul_md(path: Path | str) -> SoulData:
    """Parse a SOUL.md file and return the structured data.

    Returns an empty SoulData if the file does not exist.
    Extracts **Name.** Body text patterns (same regex as Rust/Python).
    """
    empty = SoulData()

    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return empty

    if not content:
        return empty

    # Extract title from first `# ` heading.
    title: str | None = None
    for line in content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # Extract description.
    description = _extract_description(content)

    # Extract **Name.** Body text patterns, with optional <agents>tag</agents>.
    principles: list[SoulPrinciple] = []
    for m in _PRINCIPLE_RE.finditer(content):
        raw_text = m.group(2).strip()
        agents_match = _AGENTS_RE.search(raw_text)
        agents = [s.strip() for s in agents_match.group(1).split(",")] if agents_match else ["all"]
        text = _AGENTS_RE.sub("", raw_text).strip()
        principles.append(SoulPrinciple(name=m.group(1), text=text, agents=agents))

    return SoulData(principles=principles, title=title, description=description)


def _extract_description(content: str) -> str | None:
    """Extract description text between the title line and the first **bold** principle."""
    lines = content.splitlines()

    # Find title line index.
    title_idx: int | None = None
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title_idx = i
            break

    if title_idx is None:
        return None

    # Find first principle line.
    first_principle_idx: int | None = None
    for i, line in enumerate(lines):
        if line.startswith("**") and ".**" in line:
            first_principle_idx = i
            break

    end = first_principle_idx if first_principle_idx is not None else len(lines)

    # Collect non-empty lines between title and first principle.
    desc_lines = [line for line in lines[title_idx + 1 : end] if line.strip()]

    if not desc_lines:
        return None
    return " ".join(desc_lines)
