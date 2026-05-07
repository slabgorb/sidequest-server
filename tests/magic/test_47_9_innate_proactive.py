"""Story 47-9 — Force first innate_v1 firing on Coyote Star with GM-panel observability.

The magic subsystem is wired end-to-end but never invoked in play: 1 working_log
entry across 7 saves / 111 turns. Architect audit (2026-05-07) identified the
break at the prompt level — the narrator's CRITICAL MAGIC RULE is reactive
("emit magic_working when prose depicts a working") with no proactive trigger
to depict one. innate_v1 has fired zero times because nothing in the prose flow
stresses a character into reflexive surfacing.

This test file enforces the proactive-prompt contract:

* AC1: ``build_magic_context_block`` injects an innate_v1 worked example when
  the plugin is active (and omits it when not).
* AC2: ``Orchestrator.build_narrator_prompt`` produces a CRITICAL MAGIC RULE
  with proactive language on innate-active worlds (and reactive-only when not).
* AC3: ``coyote_star/openings.yaml`` includes at least one opening that scripts
  an inevitable innate working on turn 1, with sanity (the innate cost bar) as
  the magic_microbleed cost_bar and PC-anchored prose.
* AC4+AC5: end-to-end wiring — the strengthened prompt + a synthesized narrator
  response that follows the worked-example shape produces a magic.working span,
  appends to working_log, and debits the sanity bar.
* AC7: regression check — after apply_magic_working, the snapshot survives a
  SqliteStore roundtrip with working_log and ledger value preserved.

AC6 (GM dashboard ``just otel`` verification) is a manual step covered in the
TEA Assessment; it cannot be automated without a running orchestrator + browser.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from sidequest.agents.claude_client import ClaudeClient
from sidequest.agents.narrator import NARRATOR_OUTPUT_ONLY
from sidequest.agents.orchestrator import NarratorPromptTier, Orchestrator, TurnContext
from sidequest.magic.context_builder import build_magic_context_block
from sidequest.magic.models import (
    HardLimit,
    LedgerBarSpec,
    StatusPromotion,
    WorldKnowledge,
    WorldMagicConfig,
)
from sidequest.magic.state import BarKey, MagicState

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


def _world_config_innate_active() -> WorldMagicConfig:
    """Coyote Star shape — innate_v1 + item_legacy_v1 active."""
    return WorldMagicConfig(
        world_slug="coyote_star",
        genre_slug="space_opera",
        allowed_sources=["innate", "item_based"],
        active_plugins=["innate_v1", "item_legacy_v1"],
        intensity=0.25,
        world_knowledge=WorldKnowledge(primary="classified", local_register="folkloric"),
        visibility={"primary": "feared", "local_register": "dismissed"},
        hard_limits=[HardLimit(id="psionics_never_decisive", description="x")],
        cost_types=["sanity", "notice"],
        ledger_bars=[
            LedgerBarSpec(
                id="sanity",
                scope="character",
                direction="down",
                range=(0.0, 1.0),
                threshold_low=0.40,
                consequence_on_low_cross="auto-fire The Bleeding-Through",
                starts_at_chargen=1.0,
                promote_to_status=StatusPromotion(text="Bleeding through", severity="Wound"),
            ),
            LedgerBarSpec(
                id="notice",
                scope="character",
                direction="up",
                range=(0.0, 1.0),
                threshold_high=0.75,
                starts_at_chargen=0.0,
            ),
        ],
        narrator_register="feared and folkloric",
    )


def _world_config_item_only() -> WorldMagicConfig:
    """Caverns-shape config — only item_legacy_v1 active, no innate_v1."""
    return WorldMagicConfig(
        world_slug="caverns_sunden",
        genre_slug="caverns_and_claudes",
        allowed_sources=["item_based"],
        active_plugins=["item_legacy_v1"],
        intensity=0.30,
        world_knowledge=WorldKnowledge(primary="folkloric"),
        visibility={"primary": "feared", "local_register": "dismissed"},
        hard_limits=[HardLimit(id="no_resurrection", description="death is permanent")],
        cost_types=["components", "backlash"],
        ledger_bars=[
            LedgerBarSpec(
                id="backlash",
                scope="character",
                direction="up",
                range=(0.0, 1.0),
                threshold_high=0.70,
                starts_at_chargen=0.0,
            ),
        ],
        narrator_register="folkloric",
    )


def _make_canned_client(canned_result: str) -> ClaudeClient:
    """Build a ClaudeClient whose subprocess returns the supplied canned string."""

    async def spawn_fn(command: str, *args: str, env: Any = None, **kwargs: Any):
        class FakeProcess:
            returncode = 0

            async def communicate(self):
                payload = {
                    "result": canned_result,
                    "session_id": "test-session-47-9",
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                }
                return json.dumps(payload).encode(), b""

            def kill(self):
                pass

            async def wait(self):
                return 0

        return FakeProcess()

    return ClaudeClient(spawn_fn=spawn_fn)


@pytest.fixture
def captured_watcher_events(monkeypatch) -> Iterator[list[dict[str, Any]]]:
    """Intercept ``narration_apply._watcher_publish`` calls — same shape as
    tests/magic/test_e2e_solo_scenario.py."""
    captured: list[dict[str, Any]] = []

    def _capture(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append(
            {
                "event_type": event_type,
                "fields": fields,
                "component": component,
                "severity": severity,
            }
        )

    from sidequest.server import narration_apply

    monkeypatch.setattr(narration_apply, "_watcher_publish", _capture)
    yield captured


# ---------------------------------------------------------------------------
# AC1 — context_builder injects innate worked example
# ---------------------------------------------------------------------------


def test_context_block_includes_innate_v1_worked_example_when_active():
    """When innate_v1 ∈ active_plugins, the magic-context block includes a
    worked example showing stress → reflexive surfacing → sanity cost → the
    magic_working JSON shape (AC1)."""
    config = _world_config_innate_active()
    state = MagicState.from_config(config)
    state.add_character("sira_mendes")
    block = build_magic_context_block(magic_state=state, actor_id="sira_mendes")

    # Worked example must be present and labelled
    assert "Example" in block or "EXAMPLE" in block or "example" in block, (
        "Block should include a 'Example' marker introducing the innate_v1 worked example. "
        "Got block:\n" + block
    )
    # The example must show the plugin name and the innate_v1-required fields
    assert "innate_v1" in block, "Worked example must name plugin=innate_v1"
    assert "consent_state" in block, (
        "Worked example must show consent_state — the innate_v1-required field "
        "that signals reflexive vs willing surfacing"
    )
    assert "involuntary" in block.lower(), (
        "Worked example must demonstrate the involuntary (reflexive) consent_state — "
        "this is the shape we want narrator to emit on stress-triggered surfacing"
    )
    assert "flavor" in block, (
        "Worked example must show the flavor field (acquired|born_to_it|trained_register|covenant_lineage)"
    )
    # Cost shape must include sanity (the innate cost bar)
    assert "sanity" in block, "Worked example must show sanity as the cost being debited"
    # JSON-shape marker — give Claude the literal pattern to match
    assert (
        '"plugin": "innate_v1"' in block
        or '"plugin":"innate_v1"' in block
        or 'plugin: innate_v1' in block
    ), "Worked example must show the literal magic_working JSON shape"


def test_context_block_omits_innate_example_when_only_item_legacy_active():
    """Negative: when innate_v1 NOT in active_plugins, no innate-specific
    worked example surfaces (AC1, schema purity)."""
    config = _world_config_item_only()
    state = MagicState.from_config(config)
    state.add_character("kael")
    block = build_magic_context_block(magic_state=state, actor_id="kael")

    # No innate-only fields should leak into a non-innate world's prompt
    assert "consent_state" not in block, (
        "consent_state is innate_v1-specific — must not appear when innate_v1 is not active"
    )
    assert "involuntary" not in block.lower(), (
        "Reflexive-surfacing language is innate-only; must not appear in non-innate worlds"
    )


# ---------------------------------------------------------------------------
# AC2 — narrator CRITICAL MAGIC RULE is proactive on innate-active worlds
# ---------------------------------------------------------------------------


async def test_narrator_prompt_uses_proactive_language_on_innate_world():
    """Orchestrator.build_narrator_prompt produces text that PROACTIVELY directs
    Claude to consider innate magic when prose depicts stress — not merely emit
    when prose already depicts a working (AC2)."""
    config = _world_config_innate_active()
    state = MagicState.from_config(config)
    state.add_character("sira_mendes")

    canned = "**Galley**\n\nNothing.\n\n```game_patch\n{}\n```"
    orch = Orchestrator(client=_make_canned_client(canned))
    context = TurnContext(character_name="sira_mendes", magic_state=state)
    prompt, _ = await orch.build_narrator_prompt(
        "the airlock hisses open and a stranger steps in",
        context,
        tier=NarratorPromptTier.Full,
    )

    # Strong proactive markers — at least one must appear. "every" and
    # "involuntary" are intentionally excluded: "every" is generic boilerplate
    # ("every status", "every adversary") and "involuntary" already appears
    # in the magic_working JSON-shape documentation. The markers below are
    # absent in the current reactive rule and present in the rewrite.
    proactive_markers = [
        "consider",          # "MUST consider whether reflexive innate flavor surfaces"
        "may surface",
        "reflexive",
        "stress",            # "every PC action under stress"
        "under stress",
        "must consider",
        "should surface",
        "stress-triggered",
    ]
    found = [m for m in proactive_markers if m.lower() in prompt.lower()]
    assert found, (
        f"Expected at least one proactive magic-language marker in narrator prompt; "
        f"found none. Markers searched: {proactive_markers}. The CRITICAL MAGIC RULE "
        f"must instruct narrator to volunteer innate surfacing on stress, not just "
        f"emit when prose already depicts a working."
    )


async def test_narrator_prompt_does_not_force_innate_when_innate_not_active():
    """Negative: when innate_v1 is NOT active, the proactive innate-flavor
    instruction must NOT appear (AC2 schema purity — the proactive rule is
    plugin-conditional)."""
    config = _world_config_item_only()
    state = MagicState.from_config(config)
    state.add_character("kael")

    canned = "**Cave**\n\nNothing.\n\n```game_patch\n{}\n```"
    orch = Orchestrator(client=_make_canned_client(canned))
    context = TurnContext(character_name="kael", magic_state=state)
    prompt, _ = await orch.build_narrator_prompt(
        "you take a step into the darkness", context, tier=NarratorPromptTier.Full
    )

    # The innate-specific reflexive-surfacing language must not leak into
    # an item-only world's prompt.
    forbidden = ["reflexive innate", "involuntary surfacing"]
    leaked = [f for f in forbidden if f.lower() in prompt.lower()]
    assert not leaked, (
        f"Innate-specific proactive instruction leaked into a non-innate world's prompt: "
        f"{leaked}. The proactive rule must be conditional on innate_v1 ∈ active_plugins."
    )


def test_narrator_output_only_documents_magic_working_field():
    """Sanity/regression: NARRATOR_OUTPUT_ONLY still documents magic_working."""
    assert "magic_working" in NARRATOR_OUTPUT_ONLY
    assert "CRITICAL MAGIC RULE" in NARRATOR_OUTPUT_ONLY


# ---------------------------------------------------------------------------
# AC3 — coyote_star openings.yaml has scripted innate-firing opening
# ---------------------------------------------------------------------------


def _coyote_star_openings_path() -> Path:
    """Resolve openings.yaml from SIDEQUEST_GENRE_PACKS or sibling content repo."""
    base = os.environ.get("SIDEQUEST_GENRE_PACKS")
    if base:
        candidate = Path(base) / "space_opera/worlds/coyote_star/openings.yaml"
        if candidate.is_file():
            return candidate
    # Fallback: sibling sidequest-content checkout (oq-1/oq-2 layout)
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "sidequest-content/genre_packs/space_opera/worlds/coyote_star/openings.yaml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Could not locate coyote_star/openings.yaml — set SIDEQUEST_GENRE_PACKS or "
        "run from a checkout with sidequest-content sibling"
    )


def test_coyote_star_has_scripted_innate_firing_opening():
    """At least one Coyote Star opening must script an innate working on turn 1
    by setting magic_microbleed.cost_bar='sanity' (the innate cost bar) AND
    addressing the PC directly in the detail prose (PC-anchored, not ambient bleed) (AC3)."""
    path = _coyote_star_openings_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    openings = data.get("openings", [])
    assert openings, f"openings.yaml has no openings entries: {path}"

    sanity_anchored: list[dict[str, Any]] = []
    for op in openings:
        mb = op.get("magic_microbleed")
        if not mb:
            continue
        if mb.get("cost_bar") != "sanity":
            continue
        detail = (mb.get("detail") or "").lower()
        # PC-anchored: detail addresses the player situationally (second-person
        # pronouns referring to the PC's stance / immediate surroundings).
        # Internal-perception markers ("your mind", "your senses", "behind your
        # eyes") are deliberately excluded — narrating the PC's perception
        # violates NARRATOR_AGENCY (internal cognition belongs to the player).
        # An opening that anchors via reflex stimulus + situation pronouns is
        # SOUL-compliant; one that anchors via "you feel..." is not.
        pc_anchors = [
            "you ",  # second-person you (situational)
            "your ",  # second-person possessive (most uses are situational)
            "yourself",
        ]
        reflexive_markers = [
            "reflexive",
            "surface",
            "surges",
            "surge",
            "involuntary",
            "flinch",
            "shudder",
            "uncanny",
        ]
        pc_anchored = any(a in detail for a in pc_anchors)
        reflexive = any(m in detail for m in reflexive_markers)
        if pc_anchored and reflexive:
            sanity_anchored.append(op)

    assert sanity_anchored, (
        "Expected at least one Coyote Star opening with magic_microbleed.cost_bar='sanity' "
        "AND PC-anchored reflexive-surfacing detail (second-person + reflexive/surface/"
        "involuntary language). None found in:\n  "
        + "\n  ".join(
            f"{op.get('id', '?')}: cost_bar={op.get('magic_microbleed', {}).get('cost_bar')}"
            for op in openings
        )
    )


# ---------------------------------------------------------------------------
# AC4 + AC5 — end-to-end wiring: strengthened prompt + worked-example response
# produces magic.working span, appends to working_log, debits sanity bar
# ---------------------------------------------------------------------------


async def test_innate_firing_emits_span_and_debits_sanity_bar(
    captured_watcher_events: list[dict[str, Any]],
):
    """End-to-end wiring: when the narrator emits a magic_working that follows
    the worked-example shape (AC1), the apply pipeline produces a magic.working
    span (AC4), appends a working_log entry (AC5), and debits the sanity bar
    below its chargen value of 1.0 (AC5).

    This test exercises the apply pipeline directly with a synthesized narrator
    output that matches the shape we expect the strengthened prompt to elicit.
    The architect's audit confirmed the apply pipeline is intact; this test
    pins the end-state contract that the prompt fix must produce."""
    from sidequest.game.session import GameSnapshot
    from sidequest.server.narration_apply import apply_magic_working

    config = _world_config_innate_active()
    state = MagicState.from_config(config)
    state.add_character("sira_mendes")
    snapshot = GameSnapshot.model_construct(magic_state=state)

    # The shape the strengthened context-block worked example teaches Claude
    # to emit. If the worked example diverges from this shape, AC1 should
    # also fail — they're locked together.
    magic_working = {
        "plugin": "innate_v1",
        "mechanism": "condition",
        "actor": "sira_mendes",
        "costs": {"sanity": 0.15},
        "domain": "psychic",
        "narrator_basis": "reflexive recoil from uncanny presence",
        "flavor": "acquired",
        "consent_state": "involuntary",
    }
    result = apply_magic_working(snapshot=snapshot, patch_field=magic_working)

    # AC4: magic.working span emitted via watcher route
    matching_events = [
        e
        for e in captured_watcher_events
        if e["component"] == "magic"
        and e["event_type"] == "state_transition"
        and e["fields"].get("op") == "working"
    ]
    assert len(matching_events) == 1, (
        f"Expected exactly one magic.working span; got {len(matching_events)}. "
        f"All captured: {captured_watcher_events}"
    )
    fields = matching_events[0]["fields"]
    assert fields["plugin"] == "innate_v1"
    assert fields["actor"] == "sira_mendes"
    # ledger_after must include the post-debit sanity value (< 1.0)
    assert "sanity" in fields["ledger_after"]
    assert fields["ledger_after"]["sanity"] < 1.0

    # AC5: working_log appended
    assert len(snapshot.magic_state.working_log) == 1
    log_entry = snapshot.magic_state.working_log[0]
    assert log_entry.plugin == "innate_v1"
    assert log_entry.actor == "sira_mendes"

    # AC5: sanity bar debited below 1.0 (chargen default)
    sanity = snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    )
    assert sanity.value < 1.0, (
        f"sanity bar must debit below chargen value of 1.0; got {sanity.value}"
    )
    assert sanity.value == pytest.approx(0.85), (
        f"sanity should be 1.0 - 0.15 = 0.85; got {sanity.value}"
    )

    # No DEEP_RED flags on a clean firing
    deep_red = [f for f in result.flags if f.severity.value == "deep_red"]
    assert deep_red == [], f"Clean firing must not raise DEEP_RED flags; got {deep_red}"


# ---------------------------------------------------------------------------
# AC7 — save/load roundtrip preserves working_log + ledger after firing
# ---------------------------------------------------------------------------


def test_save_load_roundtrip_preserves_working_log_and_sanity():
    """After apply_magic_working, the snapshot serializes and deserializes via
    SqliteStore with working_log entries and sanity bar value preserved (AC7,
    regression-protection — this contract must continue to hold post-47-9)."""
    from sidequest.game.persistence import SqliteStore
    from sidequest.game.session import GameSnapshot
    from sidequest.server.narration_apply import apply_magic_working

    config = _world_config_innate_active()
    state = MagicState.from_config(config)
    state.add_character("sira_mendes")
    snapshot = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        magic_state=state,
    )

    apply_magic_working(
        snapshot=snapshot,
        patch_field={
            "plugin": "innate_v1",
            "mechanism": "condition",
            "actor": "sira_mendes",
            "costs": {"sanity": 0.18},
            "domain": "psychic",
            "narrator_basis": "uncanny resonance washes through her",
            "flavor": "acquired",
            "consent_state": "involuntary",
        },
    )

    pre_log_len = len(snapshot.magic_state.working_log)
    pre_sanity = snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    ).value
    assert pre_log_len == 1
    assert pre_sanity == pytest.approx(0.82)

    # Roundtrip via the same code path production uses (in-memory store mirrors
    # the SQLite file path; only the sqlite3 connection differs).
    store = SqliteStore.open_in_memory()
    store.init_session("space_opera", "coyote_star")
    store.save(snapshot)
    saved = store.load()

    assert saved is not None, "SqliteStore.load() must rehydrate the saved session"
    assert saved.snapshot.magic_state is not None
    assert len(saved.snapshot.magic_state.working_log) == pre_log_len
    loaded_log_entry = saved.snapshot.magic_state.working_log[0]
    assert loaded_log_entry.plugin == "innate_v1"
    assert loaded_log_entry.actor == "sira_mendes"

    loaded_sanity = saved.snapshot.magic_state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    ).value
    assert loaded_sanity == pytest.approx(pre_sanity), (
        f"sanity bar must roundtrip; pre={pre_sanity}, post={loaded_sanity}"
    )
