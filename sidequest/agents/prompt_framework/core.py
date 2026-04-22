"""Prompt framework core — PromptComposer protocol and PromptRegistry implementation.

Port of sidequest-agents/src/prompt_framework/mod.rs (everything beyond types + soul).

PromptComposer is the abstract protocol (Python Protocol class, analogous to the
Rust trait). PromptRegistry is the concrete implementation that stores sections
per-agent and composes them in attention-zone order.

Note: Methods that depend on sidequest.game types (register_ocean_personalities_section,
register_ability_context, register_knowledge_section, register_resource_section,
register_pacing_section, register_scene_directive) are included but only those
types already ported in Phase 1 are used.  The remaining helpers that reference
sidequest_game types not yet in Phase 1 (e.g. SceneDirective, KnownFact, Npc)
are gated by TYPE_CHECKING so the registry is still importable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sidequest.agents.prompt_framework.types import (
    AttentionZone,
    PromptSection,
    SectionCategory,
)

if TYPE_CHECKING:
    from sidequest.game.session import NpcRegistryEntry


# ---------------------------------------------------------------------------
# Protocol (Rust trait → Python Protocol)
# ---------------------------------------------------------------------------


@runtime_checkable
class PromptComposer(Protocol):
    """Protocol for assembling prompt sections into a final prompt string.

    Implementors register sections and compose them in attention-optimal zone order.
    Port of the Rust PromptComposer trait.
    """

    def register_section(self, agent_name: str, section: PromptSection) -> None:
        """Register a section for a given agent."""
        ...

    def registry(self, agent_name: str) -> list[PromptSection]:
        """Return sections for an agent in insertion order."""
        ...

    def get_sections(
        self,
        agent_name: str,
        category: SectionCategory | None = None,
        zone: AttentionZone | None = None,
    ) -> list[PromptSection]:
        """Return sections filtered by optional category and/or zone."""
        ...

    def compose(self, agent_name: str) -> str:
        """Compose all registered sections for an agent into a final prompt string."""
        ...

    def clear(self, agent_name: str) -> None:
        """Clear all sections for an agent."""
        ...


# ---------------------------------------------------------------------------
# Agents that receive pacing/verbosity guidance (post ADR-067: only narrator)
# ---------------------------------------------------------------------------

_PACING_AGENTS: frozenset[str] = frozenset(["narrator"])
_NARRATING_AGENTS: frozenset[str] = frozenset(["narrator"])


# ---------------------------------------------------------------------------
# PromptRegistry — concrete PromptComposer implementation
# ---------------------------------------------------------------------------


class PromptRegistry:
    """Concrete implementation of PromptComposer.

    Stores sections per agent, composes in zone order.
    Port of sidequest-agents PromptRegistry.
    """

    def __init__(self) -> None:
        self._sections: dict[str, list[PromptSection]] = {}

    # ------------------------------------------------------------------
    # PromptComposer protocol implementation
    # ------------------------------------------------------------------

    def register_section(self, agent_name: str, section: PromptSection) -> None:
        """Register a section for a given agent."""
        self._sections.setdefault(agent_name, []).append(section)

    def registry(self, agent_name: str) -> list[PromptSection]:
        """Return sections for an agent in insertion order."""
        return list(self._sections.get(agent_name, []))

    def get_sections(
        self,
        agent_name: str,
        category: SectionCategory | None = None,
        zone: AttentionZone | None = None,
    ) -> list[PromptSection]:
        """Return sections filtered by optional category and/or zone."""
        sections = self._sections.get(agent_name, [])
        result = []
        for s in sections:
            if category is not None and s.category != category:
                continue
            if zone is not None and s.zone != zone:
                continue
            result.append(s)
        return result

    def compose(self, agent_name: str) -> str:
        """Compose all registered sections for an agent into a final prompt string.

        Sorts by zone order before joining — same as Rust implementation.
        """
        sections = list(self._sections.get(agent_name, []))
        sections.sort(key=lambda s: s.zone.order())
        non_empty = [s.content for s in sections if not s.is_empty()]
        return "\n\n".join(non_empty)

    def clear(self, agent_name: str) -> None:
        """Clear all sections for an agent."""
        self._sections.pop(agent_name, None)

    # ------------------------------------------------------------------
    # High-level registration helpers (port of PromptRegistry impl block)
    # ------------------------------------------------------------------

    def register_pacing_section(
        self,
        agent_name: str,
        narrator_directive: str,
        escalation_beat: str | None = None,
    ) -> None:
        """Inject pacing guidance into the prompt for narrating agents.

        Non-narrating agents are silently skipped.
        """
        if agent_name not in _PACING_AGENTS:
            return

        content = f"## Pacing Guidance\n{narrator_directive}"
        if escalation_beat:
            content += f"\n\n## Escalation Beat\n{escalation_beat}"

        self.register_section(
            agent_name,
            PromptSection.new(
                "pacing",
                content,
                AttentionZone.Late,
                SectionCategory.Context,
            ),
        )

    def register_verbosity_section(self, agent_name: str, verbosity: str) -> None:
        """Inject narrator verbosity instructions into the system prompt.

        Only applies to narrating agents. Non-narrating agents are silently skipped.
        Story 14-3: Per-session verbosity control.

        verbosity: one of 'concise', 'standard', 'verbose'
        """
        if agent_name not in _NARRATING_AGENTS:
            return

        _VERBOSITY_MAP: dict[str, str] = {
            "concise": (
                "<critical>\n"
                "<length-limit>\n"
                "HARD LIMIT: Maximum 4 sentences of prose. DO NOT EXCEED 400 characters of narrative text.\n"
                "This overrides all other length guidance. If a trope beat or genre instruction "
                "would push you past this limit, cut description — never cut the limit.\n"
                "Action and consequence only. No atmosphere. No sensory detail.\n"
                "The game_patch JSON does not count toward this limit.\n"
                "</length-limit>\n"
                "</critical>"
            ),
            "verbose": (
                "<critical>\n"
                "<length-limit>\n"
                "HARD LIMIT: Maximum 10 sentences of prose. DO NOT EXCEED 1000 characters of narrative text.\n"
                "This overrides all other length guidance. If a trope beat or genre instruction "
                "would push you past this limit, cut description — never cut the limit.\n"
                "Rich atmosphere for arrivals and reveals. Shorter for simple actions.\n"
                "The game_patch JSON does not count toward this limit.\n"
                "</length-limit>\n"
                "</critical>"
            ),
        }
        # Default (standard) or unknown values fall back to standard.
        content = _VERBOSITY_MAP.get(
            verbosity,
            (
                "<critical>\n"
                "<length-limit>\n"
                "HARD LIMIT: Maximum 6 sentences of prose. DO NOT EXCEED 600 characters of narrative text.\n"
                "This overrides all other length guidance. If a trope beat, genre voice instruction, "
                "or MUST-weave directive would push you past this limit, cut description — never cut the limit.\n"
                "One short paragraph for simple actions. Two short paragraphs for arrivals or reveals.\n"
                "The game_patch JSON block does not count toward this limit.\n"
                "Count your sentences before responding. If you have more than 6, cut.\n"
                "</length-limit>\n"
                "</critical>"
            ),
        )

        self.register_section(
            agent_name,
            PromptSection.new(
                "narrator_verbosity",
                content,
                AttentionZone.Recency,
                SectionCategory.Guardrail,
            ),
        )

    def register_vocabulary_section(self, agent_name: str, vocabulary: str) -> None:
        """Inject narrator vocabulary/complexity instructions.

        Only applies to narrating agents. Non-narrating agents are silently skipped.
        Story 14-4: Per-session vocabulary control.

        vocabulary: one of 'accessible', 'literary', 'epic'
        """
        if agent_name not in _NARRATING_AGENTS:
            return

        _VOCAB_MAP: dict[str, str] = {
            "accessible": (
                "[NARRATION VOCABULARY]\n"
                "Use simple, direct language. Prefer common words over obscure "
                "ones. Keep sentences short and clear. Aim for approximately "
                "8th-grade reading level. No archaic constructions or elaborate "
                "metaphors."
            ),
            "epic": (
                "[NARRATION VOCABULARY]\n"
                "Use elevated, archaic, or mythic diction. Embrace elaborate "
                "sentence structures, rare words, and poetic constructions. "
                "Channel the cadence of sagas, epics, and high fantasy prose. "
                "Unrestricted complexity."
            ),
        }
        content = _VOCAB_MAP.get(
            vocabulary,
            (
                "[NARRATION VOCABULARY]\n"
                "Use rich but clear prose. Employ varied vocabulary and literary "
                "devices where they serve the narrative. Balance elegance with "
                "accessibility — vivid but not purple."
            ),
        )

        self.register_section(
            agent_name,
            PromptSection.new(
                "narrator_vocabulary",
                content,
                AttentionZone.Late,
                SectionCategory.Format,
            ),
        )

    def register_footnote_protocol_section(self, agent_name: str) -> None:
        """Inject the footnote protocol instruction into the narrator prompt.

        Story 9-11.
        """
        content = """\
[FOOTNOTE PROTOCOL]
When you reveal new information or reference something the party previously learned,
include a numbered marker in your prose like [1], [2], etc.

For each marker, emit a footnote in your structured output with:
- summary: one-sentence description of the fact
- category: one of Lore, Place, Person, Quest, Ability
- is_new: true if this is a new revelation, false if referencing prior knowledge

Example prose: "As you enter the grove, Reva feels a deep wrongness [1]."
Example footnote: { "marker": 1, "summary": "Corruption detected in the grove", "category": "Place", "is_new": true }

If you reference something the party already knows, set is_new to false and include the fact_id.
If nothing new is revealed and nothing prior is referenced, omit the footnotes array entirely."""

        self.register_section(
            agent_name,
            PromptSection.new(
                "footnote_protocol",
                content,
                AttentionZone.Late,
                SectionCategory.Format,
            ),
        )

    def register_resource_section(
        self,
        agent_name: str,
        declarations: list[dict],  # ResourceDeclaration-like dicts
        state: dict[str, float],
    ) -> None:
        """Inject genre resource state into the narrator prompt (story 16-1).

        Serializes current resource values into a human-readable block in the Valley zone.
        Empty declarations produce no section.

        Each declaration dict must have: name, label, starting, max, voluntary, decay_per_turn.
        """
        if not declarations:
            return

        lines = ["## GENRE RESOURCES — Current State"]
        for decl in declarations:
            current = state.get(decl["name"], decl["starting"])
            vol_label = "voluntary" if decl.get("voluntary", True) else "involuntary"
            line = f"{decl['label']}: {current}/{decl['max']} ({vol_label})"
            decay = decl.get("decay_per_turn", 0.0)
            if abs(decay) > 1e-9:
                line += f", decay {abs(decay)}/turn"
            lines.append(line)

        self.register_section(
            agent_name,
            PromptSection.new(
                "genre_resources",
                "\n".join(lines),
                AttentionZone.Valley,
                SectionCategory.State,
            ),
        )

    def register_npc_roster_section(
        self,
        agent_name: str,
        npc_registry: list[NpcRegistryEntry],
    ) -> None:
        """Inject canonical NPC identity data into the narrator prompt.

        Story 37-44: without this section the narrator cannot see the
        registry and reinvents pronouns / role / appearance each turn
        (playtest 3: Frandrew drifted from "she/her captain" to "he/him
        grease monkey" across 10 turns).

        Empty registry produces no section (zero-byte leak). Entries are
        rendered one-per-line with name, pronouns, role, appearance, and
        last_seen_location so the narrator has ground truth every turn.

        Placed in the Early zone (not Valley): identity is acute data,
        not background lore — if it drifts to Valley the narrator attends
        to it less over long sessions, which is the exact drift we saw.
        """
        if not npc_registry:
            return

        lines = ["## KNOWN NPCS — Canonical Identity (do not contradict)"]
        for entry in npc_registry:
            parts: list[str] = [entry.name]
            tags: list[str] = []
            if entry.pronouns:
                tags.append(entry.pronouns)
            if entry.role:
                tags.append(entry.role)
            if tags:
                parts.append(f"({', '.join(tags)})")
            if entry.appearance:
                parts.append(f"— {entry.appearance}")
            if entry.last_seen_location:
                parts.append(f"[last seen: {entry.last_seen_location}]")
            lines.append("- " + " ".join(parts))
        lines.append(
            "Use these exact pronouns and roles. Physical identity is "
            "canonical; only emotional perception is POV."
        )

        self.register_section(
            agent_name,
            PromptSection.new(
                "npc_roster",
                "\n".join(lines),
                AttentionZone.Early,
                SectionCategory.State,
            ),
        )
