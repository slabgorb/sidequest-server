"""Cleric reliquary block in the narrator pre-prompt.

The Cleric's ``<available-reliquaries>`` section is emitted only when
every gate is met:

* actor has a ``divine_favor`` character-scope bar (Cleric class);
* the bar's value is at or above
  ``DEFAULT_DIVINE_FAVOR_THRESHOLD`` (0.7);
* the actor has not already spent the session's free reliquary use;
* the world catalog ships at least one reliquary with a non-empty
  ``divine_favor_effect`` field.

These tests pin the gate matrix and the rendered block shape so the
narrator sees the verbatim effect text rather than hallucinating one
that doesn't exist in canon.
"""

from __future__ import annotations

from sidequest.genre.models.items import WorldItemsCatalog
from sidequest.magic.context_builder import build_magic_context_block
from sidequest.magic.models import LedgerBarSpec, WorldKnowledge, WorldMagicConfig
from sidequest.magic.state import BarKey, MagicState


def _cleric_world_config() -> WorldMagicConfig:
    return WorldMagicConfig(
        world_slug="caverns_sunden_test",
        genre_slug="caverns_and_claudes",
        allowed_sources=["learned"],
        active_plugins=["learned_v1"],
        intensity=0.5,
        world_knowledge=WorldKnowledge(primary="folkloric"),
        visibility={"primary": "open"},
        cost_types=["divine_favor"],
        narrator_register="test",
        ledger_bars=[
            LedgerBarSpec(
                id="divine_favor",
                scope="character",
                direction="up",
                range=(0.0, 1.0),
                threshold_low=0.1,
                threshold_high=0.7,
                consequence_on_low_cross="restore at Confessional",
                starts_at_chargen={
                    "Cleric": 0.5,
                    "Fighter": 0.0,
                    "Thief": 0.0,
                    "Mage": 0.0,
                },
            ),
        ],
        hard_limits=[],
    )


def _reliquary_catalog() -> WorldItemsCatalog:
    return WorldItemsCatalog.model_validate(
        {
            "world": "caverns_sunden_test",
            "reliquaries": [
                {
                    "id": "confessional_alms_bowl",
                    "name": "Anselm Vail's Confessional Alms-Bowl",
                    "divine_favor_effect": (
                        "At divine_favor >= 0.7 the Cleric may divert one "
                        "approaching count-event."
                    ),
                },
                {
                    # No divine_favor_effect — should NOT appear in the block.
                    "id": "broken_reliquary",
                    "name": "Broken Reliquary",
                },
            ],
        }
    )


def _cleric_state(favor: float) -> MagicState:
    state = MagicState.from_config(_cleric_world_config())
    state.add_character("anselm", character_class="Cleric")
    state.set_bar_value(
        BarKey(scope="character", owner_id="anselm", bar_id="divine_favor"), favor
    )
    return state


# ---------------------------------------------------------------------------
# Gate matrix
# ---------------------------------------------------------------------------


def test_reliquary_block_appears_when_cleric_at_threshold() -> None:
    state = _cleric_state(favor=0.7)
    block = build_magic_context_block(
        magic_state=state,
        actor_id="anselm",
        reliquaries=list(_reliquary_catalog().reliquaries),
    )
    assert "<available-reliquaries" in block
    assert "anselm" in block
    assert "divine_favor=" in block
    assert "confessional_alms_bowl" in block
    assert "Anselm Vail's Confessional Alms-Bowl" in block
    # Verbatim effect text passes through.
    assert "divert one approaching count-event" in block
    # Reliquary without divine_favor_effect is omitted.
    assert "broken_reliquary" not in block
    assert "</available-reliquaries>" in block


def test_reliquary_block_absent_when_favor_below_threshold() -> None:
    state = _cleric_state(favor=0.65)
    block = build_magic_context_block(
        magic_state=state,
        actor_id="anselm",
        reliquaries=list(_reliquary_catalog().reliquaries),
    )
    assert "<available-reliquaries" not in block
    assert "confessional_alms_bowl" not in block


def test_reliquary_block_absent_for_non_cleric() -> None:
    """Fighter / Thief / Mage have no divine_favor bar instantiated.
    The builder must NOT emit the block even when reliquaries exist."""
    state = MagicState.from_config(_cleric_world_config())
    state.add_character("rux", character_class="Fighter")
    # Fighter's starts_at_chargen for divine_favor is 0.0; the bar IS
    # instantiated by add_character. Set it to 0.0 to make the gate
    # explicit — the threshold test is the actual filter for non-Clerics.
    block = build_magic_context_block(
        magic_state=state,
        actor_id="rux",
        reliquaries=list(_reliquary_catalog().reliquaries),
    )
    assert "<available-reliquaries" not in block


def test_reliquary_block_absent_when_actor_unknown() -> None:
    """If the actor has no bars at all, the gate fails cleanly."""
    state = MagicState.from_config(_cleric_world_config())
    block = build_magic_context_block(
        magic_state=state,
        actor_id="ghost",
        reliquaries=list(_reliquary_catalog().reliquaries),
    )
    assert "<available-reliquaries" not in block


def test_reliquary_block_absent_after_free_use_spent() -> None:
    state = _cleric_state(favor=0.95)
    state.reliquary_free_use_spent.append("anselm")
    block = build_magic_context_block(
        magic_state=state,
        actor_id="anselm",
        reliquaries=list(_reliquary_catalog().reliquaries),
    )
    assert "<available-reliquaries" not in block


def test_reliquary_block_absent_when_no_reliquaries_passed() -> None:
    """A world without an items.yaml passes reliquaries=None; the
    builder must skip the section without iterating over anything."""
    state = _cleric_state(favor=0.95)
    block = build_magic_context_block(
        magic_state=state, actor_id="anselm", reliquaries=None
    )
    assert "<available-reliquaries" not in block

    block_empty = build_magic_context_block(
        magic_state=state, actor_id="anselm", reliquaries=[]
    )
    assert "<available-reliquaries" not in block_empty


def test_reliquary_block_absent_when_actor_id_missing() -> None:
    """No actor → no actor-scoped section. Mirrors how the existing
    block-actor-ledger gate behaves."""
    state = _cleric_state(favor=0.95)
    block = build_magic_context_block(
        magic_state=state,
        actor_id=None,
        reliquaries=list(_reliquary_catalog().reliquaries),
    )
    assert "<available-reliquaries" not in block


# ---------------------------------------------------------------------------
# Backwards compatibility — the old call signature must still work.
# ---------------------------------------------------------------------------


def test_default_signature_does_not_emit_reliquary_block() -> None:
    """The reliquary parameter is optional. Existing callers that pass
    only magic_state + actor_id must continue to get the same block
    they did before this feature landed."""
    state = _cleric_state(favor=0.95)
    block_default = build_magic_context_block(magic_state=state, actor_id="anselm")
    assert "<available-reliquaries" not in block_default
    # Other sections still render.
    assert "ACTIVE MAGIC CONTEXT" in block_default


# ---------------------------------------------------------------------------
# Type-shape sanity — pydantic dump access
# ---------------------------------------------------------------------------


def test_reliquary_block_handles_multiline_effect_text() -> None:
    """Reliquary effect text in the wild is often a YAML block scalar
    (multiline). The builder must render those lines indented so the
    narrator sees clean YAML inside the <available-reliquaries> tag."""
    catalog = WorldItemsCatalog.model_validate(
        {
            "reliquaries": [
                {
                    "id": "workhouse_lamp",
                    "name": "Brother Hesh's Workhouse Lamp",
                    "divine_favor_effect": (
                        "First line of the effect.\n"
                        "Second line continues here.\n"
                        "Third line wraps the explanation."
                    ),
                }
            ]
        }
    )
    state = _cleric_state(favor=0.8)
    block = build_magic_context_block(
        magic_state=state,
        actor_id="anselm",
        reliquaries=list(catalog.reliquaries),
    )
    assert "    First line of the effect." in block
    assert "    Second line continues here." in block
    assert "    Third line wraps the explanation." in block


def test_threshold_value_appears_in_block() -> None:
    """The 0.7 threshold is load-bearing for the playgroup; ensure it
    appears in the rendered prose so Sebastien can see the gate value
    in the prompt without rebuilding the source."""
    state = _cleric_state(favor=0.95)
    block = build_magic_context_block(
        magic_state=state,
        actor_id="anselm",
        reliquaries=list(_reliquary_catalog().reliquaries),
    )
    assert "0.7" in block
