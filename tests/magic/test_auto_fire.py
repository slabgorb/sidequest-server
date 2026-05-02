"""Auto-fire trigger evaluation — Story 47-3 Task 5.3.

When a working changes a character bar, the runtime checks whether any
ConfrontationDefinition's ``auto_fire_trigger`` matches the new value
and returns the firings to the dispatch pipeline.

Per the plan (2026-04-28-magic-system-coyote-reach-v1.md §5.2), the
evaluator lives alongside ConfrontationDefinition in
``sidequest.magic.confrontations`` and parses expressions of the form
``<bar_id> <op> <value>`` where op ∈ ``<=, >=, <, >, ==``.

Malformed triggers raise ValueError — no silent fallback per CLAUDE.md.
"""

from __future__ import annotations

import pytest

from sidequest.magic.confrontations import (
    ConfrontationDefinition,
    evaluate_auto_fire_triggers,
)


def _make_def(
    *,
    id: str,
    auto_fire: bool,
    auto_fire_trigger: str | None = None,
    plugin_tie_ins: list[str] | None = None,
    primary: str = "sanity",
) -> ConfrontationDefinition:
    return ConfrontationDefinition(
        id=id,
        label=id.replace("_", " ").title(),
        plugin_tie_ins=plugin_tie_ins or [],
        auto_fire=auto_fire,
        auto_fire_trigger=auto_fire_trigger,
        rounds=1,
        resource_pool={"primary": primary},
        description="x",
        outcomes={
            "clear_win": {"mandatory_outputs": ["x"]},
            "pyrrhic_win": {"mandatory_outputs": ["x"]},
            "clear_loss": {"mandatory_outputs": ["x"]},
            "refused": {"mandatory_outputs": ["x"]},
        },
    )


@pytest.fixture
def confs() -> list[ConfrontationDefinition]:
    return [
        _make_def(
            id="the_bleeding_through",
            auto_fire=True,
            auto_fire_trigger="sanity <= 0.40",
            plugin_tie_ins=["innate_v1"],
            primary="sanity",
        ),
        _make_def(
            id="the_quiet_word",
            auto_fire=True,
            auto_fire_trigger="notice >= 0.75",
            plugin_tie_ins=["innate_v1"],
            primary="notice",
        ),
    ]


def test_sanity_at_or_below_threshold_fires_bleeding_through(
    confs: list[ConfrontationDefinition],
) -> None:
    fired = evaluate_auto_fire_triggers(
        confs=confs, character_id="sira_mendes", bar_values={"sanity": 0.35}
    )
    assert any(c.id == "the_bleeding_through" for c, _ in fired)
    assert all(actor == "sira_mendes" for _, actor in fired)


def test_sanity_at_threshold_boundary_fires(confs: list[ConfrontationDefinition]) -> None:
    """``<=`` is inclusive — 0.40 fires, 0.40001 does not."""
    fired_at = evaluate_auto_fire_triggers(
        confs=confs, character_id="x", bar_values={"sanity": 0.40}
    )
    assert any(c.id == "the_bleeding_through" for c, _ in fired_at)


def test_sanity_above_threshold_does_not_fire(
    confs: list[ConfrontationDefinition],
) -> None:
    fired = evaluate_auto_fire_triggers(
        confs=confs, character_id="sira_mendes", bar_values={"sanity": 0.60}
    )
    assert all(c.id != "the_bleeding_through" for c, _ in fired)


def test_notice_above_threshold_fires_quiet_word(
    confs: list[ConfrontationDefinition],
) -> None:
    fired = evaluate_auto_fire_triggers(
        confs=confs, character_id="sira_mendes", bar_values={"notice": 0.80}
    )
    assert any(c.id == "the_quiet_word" for c, _ in fired)


def test_non_auto_fire_skipped(confs: list[ConfrontationDefinition]) -> None:
    """``auto_fire=False`` confrontations never appear in firings."""
    confs.append(_make_def(id="the_standoff", auto_fire=False))
    fired = evaluate_auto_fire_triggers(
        confs=confs, character_id="x", bar_values={"sanity": 0.10, "notice": 0.99}
    )
    assert all(c.id != "the_standoff" for c, _ in fired)


def test_unknown_bar_id_does_not_fire(confs: list[ConfrontationDefinition]) -> None:
    """Trigger references a bar the character doesn't have — silently skip the trigger.

    This is *not* a silent fallback — the confrontation simply doesn't
    have data to evaluate against. Compared against the ``no silent
    fallback`` rule, this is a no-op decision: no fallback path is
    chosen, the trigger is just inapplicable for the given actor.
    """
    fired = evaluate_auto_fire_triggers(
        confs=confs, character_id="x", bar_values={"vitality": 0.50}
    )
    assert fired == []


def test_invalid_trigger_expression_raises() -> None:
    """Lang-review #1: silent exception swallowing forbidden.

    Malformed ``auto_fire_trigger`` must raise — no silent default.
    """
    bad = _make_def(id="bad", auto_fire=True, auto_fire_trigger="not parseable")
    with pytest.raises(ValueError, match="parse"):
        evaluate_auto_fire_triggers(
            confs=[bad], character_id="x", bar_values={"sanity": 0.10}
        )


def test_returns_pairs_of_definition_and_character(
    confs: list[ConfrontationDefinition],
) -> None:
    """Type contract: list[tuple[ConfrontationDefinition, str]]."""
    fired = evaluate_auto_fire_triggers(
        confs=confs, character_id="rux", bar_values={"sanity": 0.20}
    )
    # The fixture has exactly one matching auto-fire (the_bleeding_through
    # at sanity ≤ 0.40); pin == 1 to catch over-firing regressions.
    assert len(fired) == 1
    conf, actor = fired[0]
    assert isinstance(conf, ConfrontationDefinition)
    assert isinstance(actor, str)
    assert actor == "rux"
    assert conf.id == "the_bleeding_through"
