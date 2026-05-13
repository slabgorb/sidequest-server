"""Cleric reliquary invocation — gate ordering, OTEL, once-per-session.

These tests pin the mechanical surface of
``sidequest.magic.reliquary_ops.invoke_reliquary``:

* Non-Cleric (no divine_favor bar) is blocked with a typed reason.
* divine_favor below 0.7 is blocked with a typed reason.
* Unknown / non-favor-gated reliquaries are blocked with typed reasons.
* The first invocation at or above threshold succeeds, marks the
  free-use token spent, and emits a ``magic.invoke_reliquary``
  watcher event with the bar value and the catalog ids.
* A second invocation in the same session is blocked even if
  divine_favor is still >= 0.7 (session-scoped, not rest-scoped).
* The threshold defaults to 0.7 but is configurable.

The OTEL assertions matter as much as the gate logic — the GM panel
is the lie detector that distinguishes a real invocation from a
narrator wing-it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from sidequest.genre.models.items import WorldItemsCatalog
from sidequest.magic.models import LedgerBarSpec, WorldKnowledge, WorldMagicConfig
from sidequest.magic.reliquary_ops import (
    DEFAULT_DIVINE_FAVOR_THRESHOLD,
    InvokeReliquaryResult,
    ReliquaryInvokeError,
    invoke_reliquary,
)
from sidequest.magic.state import BarKey, MagicState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cleric_config() -> WorldMagicConfig:
    """A minimal magic config that grants a ``divine_favor`` bar to Clerics."""
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
                consequence_on_low_cross="cleric must restore at Confessional",
                starts_at_chargen={"Cleric": 0.5, "Fighter": 0.0, "Thief": 0.0, "Mage": 0.0},
            ),
        ],
        hard_limits=[],
    )


def _catalog_with_alms_bowl() -> WorldItemsCatalog:
    return WorldItemsCatalog.model_validate(
        {
            "world": "caverns_sunden_test",
            "reliquaries": [
                {
                    "id": "confessional_alms_bowl",
                    "name": "Anselm Vail's Confessional Alms-Bowl",
                    "divine_register": True,
                    "keyed_to_rite": "confessional",
                    "divine_favor_effect": (
                        "At divine_favor >= 0.7 the Cleric may divert one "
                        "approaching count-event. Spends the free-reliquary-"
                        "effect for the session."
                    ),
                },
                {
                    "id": "workhouse_lamp",
                    "name": "Brother Hesh's Workhouse Lamp",
                    "keyed_to_rite": "workhouse",
                    "divine_favor_effect": "Steadies the well-fed.",
                },
                {
                    # Reliquary without a divine_favor_effect — content bug
                    # the op must surface, not silently invoke.
                    "id": "broken_reliquary",
                    "name": "Broken Reliquary",
                    "keyed_to_rite": "confessional",
                },
            ],
        }
    )


def _new_state_with_cleric(favor: float = 0.8) -> MagicState:
    state = MagicState.from_config(_cleric_config())
    state.add_character("anselm", character_class="Cleric")
    state.set_bar_value(BarKey(scope="character", owner_id="anselm", bar_id="divine_favor"), favor)
    return state


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
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

    # reliquary_ops imports publish_event by name at module load time; patch
    # both the source attribute and the local alias so the assertion sees
    # the captured invocation regardless of how the importing module bound it.
    from sidequest.magic import reliquary_ops as ro_mod
    from sidequest.telemetry import watcher_hub as hub_mod

    monkeypatch.setattr(hub_mod, "publish_event", _capture)
    monkeypatch.setattr(ro_mod, "_watcher_publish", _capture)
    yield captured


def _invoke_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event_type"] == "magic.invoke_reliquary"]


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_threshold_default_is_seven_tenths() -> None:
    """Spec lock: 0.7 is the gate, not 0.6 or 0.75. If this changes,
    the playgroup needs to know."""
    assert DEFAULT_DIVINE_FAVOR_THRESHOLD == 0.7


def test_invoke_at_threshold_succeeds_and_emits_otel(
    captured_events: list[dict],
) -> None:
    state = _new_state_with_cleric(favor=0.7)
    catalog = _catalog_with_alms_bowl()

    result = invoke_reliquary(
        state,
        actor="anselm",
        reliquary_id="confessional_alms_bowl",
        items_catalog=catalog,
    )

    assert isinstance(result, InvokeReliquaryResult)
    assert result.actor == "anselm"
    assert result.reliquary_id == "confessional_alms_bowl"
    assert result.reliquary_name == "Anselm Vail's Confessional Alms-Bowl"
    assert result.divine_favor == pytest.approx(0.7)
    assert "free-reliquary-effect" in result.effect_text

    # Spent token recorded.
    assert state.reliquary_free_use_spent == ["anselm"]

    # OTEL emitted with the right shape.
    events = _invoke_events(captured_events)
    assert len(events) == 1
    fields = events[0]["fields"]
    assert events[0]["component"] == "magic"
    assert fields["actor"] == "anselm"
    assert fields["reliquary_id"] == "confessional_alms_bowl"
    assert fields["reliquary_name"] == "Anselm Vail's Confessional Alms-Bowl"
    assert fields["divine_favor"] == pytest.approx(0.7)
    assert fields["threshold"] == pytest.approx(0.7)
    assert fields["free_use_spent_actors"] == ["anselm"]


def test_invoke_above_threshold_succeeds() -> None:
    state = _new_state_with_cleric(favor=0.95)
    result = invoke_reliquary(
        state,
        actor="anselm",
        reliquary_id="confessional_alms_bowl",
        items_catalog=_catalog_with_alms_bowl(),
    )
    assert result.divine_favor == pytest.approx(0.95)


def test_custom_threshold_lower_admits_lower_favor() -> None:
    """``threshold`` parameter is configurable. A future genre or
    homebrew could set a lower bar; verify the op honors it."""
    state = _new_state_with_cleric(favor=0.55)
    result = invoke_reliquary(
        state,
        actor="anselm",
        reliquary_id="confessional_alms_bowl",
        items_catalog=_catalog_with_alms_bowl(),
        threshold=0.5,
    )
    assert result.actor == "anselm"


# ---------------------------------------------------------------------------
# Block gates
# ---------------------------------------------------------------------------


def test_non_cleric_blocked_with_typed_reason(captured_events: list[dict]) -> None:
    state = MagicState.from_config(_cleric_config())
    # Don't add the character — they have no divine_favor bar.
    with pytest.raises(ReliquaryInvokeError) as exc_info:
        invoke_reliquary(
            state,
            actor="rux",  # not added to state
            reliquary_id="confessional_alms_bowl",
            items_catalog=_catalog_with_alms_bowl(),
        )
    assert exc_info.value.reason == "no_divine_favor_bar"
    assert _invoke_events(captured_events) == []


def test_favor_below_threshold_blocked(captured_events: list[dict]) -> None:
    state = _new_state_with_cleric(favor=0.65)
    with pytest.raises(ReliquaryInvokeError) as exc_info:
        invoke_reliquary(
            state,
            actor="anselm",
            reliquary_id="confessional_alms_bowl",
            items_catalog=_catalog_with_alms_bowl(),
        )
    assert exc_info.value.reason == "favor_below_threshold"
    assert "0.65" in str(exc_info.value) or "0.7" in str(exc_info.value)
    assert state.reliquary_free_use_spent == []  # not spent on a failed gate
    assert _invoke_events(captured_events) == []


def test_unknown_reliquary_blocked_with_known_list(captured_events: list[dict]) -> None:
    state = _new_state_with_cleric(favor=0.8)
    with pytest.raises(ReliquaryInvokeError) as exc_info:
        invoke_reliquary(
            state,
            actor="anselm",
            reliquary_id="not_a_real_reliquary",
            items_catalog=_catalog_with_alms_bowl(),
        )
    assert exc_info.value.reason == "unknown_reliquary"
    # Error lists known ids so the GM panel can render a useful hint.
    msg = str(exc_info.value)
    assert "confessional_alms_bowl" in msg
    assert "workhouse_lamp" in msg
    assert _invoke_events(captured_events) == []


def test_reliquary_without_divine_favor_effect_blocked(
    captured_events: list[dict],
) -> None:
    """A reliquary entry that doesn't declare divine_favor_effect is
    content the op can't invoke. Surface as a typed error so the
    content author sees it, not as a silent narrator wing-it."""
    state = _new_state_with_cleric(favor=0.8)
    with pytest.raises(ReliquaryInvokeError) as exc_info:
        invoke_reliquary(
            state,
            actor="anselm",
            reliquary_id="broken_reliquary",
            items_catalog=_catalog_with_alms_bowl(),
        )
    assert exc_info.value.reason == "reliquary_missing_effect"
    assert state.reliquary_free_use_spent == []
    assert _invoke_events(captured_events) == []


def test_second_invocation_in_session_blocked_even_if_favor_still_high(
    captured_events: list[dict],
) -> None:
    """Once-per-session is the heart of this feature. Even if the
    Cleric tops divine_favor back up after the first invoke, the
    session token does not refresh — that requires a new session."""
    state = _new_state_with_cleric(favor=0.95)
    catalog = _catalog_with_alms_bowl()

    invoke_reliquary(
        state,
        actor="anselm",
        reliquary_id="confessional_alms_bowl",
        items_catalog=catalog,
    )

    # Try a different reliquary the second time — the gate is on the
    # actor, not the reliquary, per the alms-bowl text "Spends the
    # free-reliquary-effect for the session" (the effect, singular).
    with pytest.raises(ReliquaryInvokeError) as exc_info:
        invoke_reliquary(
            state,
            actor="anselm",
            reliquary_id="workhouse_lamp",
            items_catalog=catalog,
        )
    assert exc_info.value.reason == "free_use_already_spent"

    # Only one OTEL event (the successful first call).
    assert len(_invoke_events(captured_events)) == 1


def test_different_actors_have_independent_free_uses() -> None:
    """The session token is per-actor — two Clerics each get their
    own one-per-session. Matches the alms-bowl wording (the wielder's
    session, not the table's)."""
    state = _new_state_with_cleric(favor=0.95)
    state.add_character("hesh", character_class="Cleric")
    state.set_bar_value(BarKey(scope="character", owner_id="hesh", bar_id="divine_favor"), 0.85)
    catalog = _catalog_with_alms_bowl()

    invoke_reliquary(
        state,
        actor="anselm",
        reliquary_id="confessional_alms_bowl",
        items_catalog=catalog,
    )
    # Hesh can still invoke even though Anselm already did.
    result = invoke_reliquary(
        state,
        actor="hesh",
        reliquary_id="workhouse_lamp",
        items_catalog=catalog,
    )
    assert result.actor == "hesh"
    assert set(state.reliquary_free_use_spent) == {"anselm", "hesh"}
