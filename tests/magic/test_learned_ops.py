import pytest


def _config_with_slots():
    """Build a WorldMagicConfig with per-actor slots_l1 bar template."""
    from sidequest.magic.models import LedgerBarSpec, WorldKnowledge, WorldMagicConfig

    return WorldMagicConfig(
        world_slug="test",
        genre_slug="test_genre",
        allowed_sources=["learned"],
        active_plugins=["learned_v1"],
        intensity=0.5,
        world_knowledge=WorldKnowledge(primary="folkloric"),
        visibility={"primary": "open"},
        cost_types=["slots_l1"],
        narrator_register="test",
        ledger_bars=[
            LedgerBarSpec(
                id="slots_l1",
                scope="character",
                direction="down",
                range=(0.0, 4.0),
                threshold_low=0.0,
                consequence_on_low_cross="out of L1 slots",
                starts_at_chargen=2.0,
            ),
        ],
        hard_limits=[],
    )


def test_prepare_populates_prepared_spells():
    from sidequest.magic.learned_ops import prepare
    from sidequest.magic.state import MagicState

    state = MagicState.from_config(_config_with_slots())
    state.add_character("rux")
    state.learn_spell("rux", "magic_missile")
    state.learn_spell("rux", "sleep")

    prepare(state, actor="rux", prep={1: ["magic_missile", "sleep"]})

    assert state.prepared_spells["rux"] == {1: ["magic_missile", "sleep"]}


def test_prepare_rejects_unknown_spell():
    from sidequest.magic.learned_ops import prepare
    from sidequest.magic.state import MagicState

    state = MagicState.from_config(_config_with_slots())
    state.add_character("rux")
    state.learn_spell("rux", "magic_missile")

    with pytest.raises(ValueError, match="not in known_spells"):
        prepare(state, actor="rux", prep={1: ["fireball"]})


def test_prepare_rejects_over_slot_budget():
    from sidequest.magic.learned_ops import prepare
    from sidequest.magic.state import MagicState

    state = MagicState.from_config(_config_with_slots())
    state.add_character("rux")
    state.learn_spell("rux", "magic_missile")
    state.learn_spell("rux", "sleep")
    state.learn_spell("rux", "charm_person")

    # slots_l1 starts at 2; preparing 3 spells should fail.
    with pytest.raises(ValueError, match="exceeds slot budget"):
        prepare(state, actor="rux", prep={1: ["magic_missile", "sleep", "charm_person"]})
