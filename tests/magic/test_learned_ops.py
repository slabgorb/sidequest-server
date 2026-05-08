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


def test_cast_decrements_slot_and_records_working():
    from sidequest.magic.learned_ops import cast, prepare
    from sidequest.magic.models import MagicWorking
    from sidequest.magic.state import BarKey, MagicState

    state = MagicState.from_config(_config_with_slots())
    state.add_character("rux")
    state.learn_spell("rux", "magic_missile")
    prepare(state, actor="rux", prep={1: ["magic_missile"]})

    working = MagicWorking(
        plugin="learned_v1",
        mechanism="studied",
        actor="rux",
        domain="physical",
        narrator_basis="cast magic missile",
        spell_id="magic_missile",
        slot_level=1,
        costs={"slots_l1": 1.0},
    )
    result = cast(state, working=working)

    bar = state.get_bar(BarKey(scope="character", owner_id="rux", bar_id="slots_l1"))
    assert bar.value == 1.0  # was 2, now 1
    assert state.working_log[-1].spell_id == "magic_missile"
    assert result.slot_consumed is True


def test_cast_rejects_unprepared_spell():
    import pytest

    from sidequest.magic.learned_ops import cast
    from sidequest.magic.models import MagicWorking
    from sidequest.magic.state import MagicState

    state = MagicState.from_config(_config_with_slots())
    state.add_character("rux")
    state.learn_spell("rux", "magic_missile")
    # Did NOT prepare anything.

    working = MagicWorking(
        plugin="learned_v1",
        mechanism="studied",
        actor="rux",
        domain="physical",
        narrator_basis="cast magic missile",
        spell_id="magic_missile",
        slot_level=1,
        costs={"slots_l1": 1.0},
    )
    with pytest.raises(ValueError, match="not prepared"):
        cast(state, working=working)


def test_cast_rejects_when_slot_empty():
    import pytest

    from sidequest.magic.learned_ops import cast, prepare
    from sidequest.magic.models import MagicWorking
    from sidequest.magic.state import MagicState

    state = MagicState.from_config(_config_with_slots())
    state.add_character("rux")
    state.learn_spell("rux", "magic_missile")
    prepare(state, actor="rux", prep={1: ["magic_missile"]})

    # Drain the slot:
    working = MagicWorking(
        plugin="learned_v1",
        mechanism="studied",
        actor="rux",
        domain="physical",
        narrator_basis="cast 1",
        spell_id="magic_missile",
        slot_level=1,
        costs={"slots_l1": 1.0},
    )
    cast(state, working=working)
    cast(state, working=working)  # 2nd cast: slot bar 2 -> 1 -> 0

    with pytest.raises(ValueError, match="no slots remaining"):
        cast(state, working=working)


def test_rest_clears_prepared_and_resets_slots():
    from sidequest.magic.learned_ops import cast, prepare, rest
    from sidequest.magic.models import MagicWorking
    from sidequest.magic.state import BarKey, MagicState

    state = MagicState.from_config(_config_with_slots())
    state.add_character("rux")
    state.learn_spell("rux", "magic_missile")
    prepare(state, actor="rux", prep={1: ["magic_missile"]})

    working = MagicWorking(
        plugin="learned_v1",
        mechanism="studied",
        actor="rux",
        domain="physical",
        narrator_basis="cast",
        spell_id="magic_missile",
        slot_level=1,
        costs={"slots_l1": 1.0},
    )
    cast(state, working=working)

    rest(state, actor="rux")

    assert state.prepared_spells["rux"] == {}
    bar = state.get_bar(BarKey(scope="character", owner_id="rux", bar_id="slots_l1"))
    assert bar.value == 2.0  # back to max
